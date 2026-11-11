#!/usr/bin/env python3
"""Compare the replayed glucose_status against what the app recorded.

For each golden cycle we feed the raw CGM window into the bridge's real pipeline
(UKF -> bucketing -> GlucoseStatusCalculatorAutoIsf) and compare:
  * glucose        vs suggested.bg
  * acceleration   vs "Parabola fit results were acceleration:X"
  * correlation    vs "... correlation:Y"
  * parabolaMinutes vs "... duration:Zm"
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.client import Bridge, load_profiles, load_treatments, load_prefs, insulin_from_settings

ROOT = Path(__file__).resolve().parents[2]
PARABOLA = re.compile(
    r"Parabola fit results were acceleration:(-?[\d.]+), correlation:([\d.]+), duration:([\d.]+)m"
)


def parse_parabola(cycle: dict) -> tuple[float, float, float] | None:
    for line in cycle.get("consoleError") or []:
        match = PARABOLA.search(line)
        if match:
            return float(match.group(1)), float(match.group(2)), float(match.group(3))
    return None


def quantiles(values: list[float]) -> str:
    if not values:
        return "n=0"
    ordered = sorted(values)
    def q(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * p))]
    return (f"n={len(ordered)} median={statistics.median(ordered):.3f} "
            f"p90={q(0.9):.3f} p99={q(0.99):.3f} max={ordered[-1]:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--from-time", default=None, help="ISO date, inclusive")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--raw-fit", action="store_true", help="promote the source to advanced filtering (raw parabola)")
    ap.add_argument("--auto-fit", action="store_true", help="recover the parabola path per cycle from the recorded fit")
    ap.add_argument("--version", default=None, help="only cycles produced by this app version")
    ap.add_argument("--timeline", action="store_true", help="per-hour exactness")
    ap.add_argument("--uploader", default=None, help="only one Nightscout uploader (libre2/native/receiver)")
    ap.add_argument("--cgm", choices=("ns", "db"), default="ns", help="CGM source: Nightscout entries or xDrip BgReadings")
    args = ap.parse_args()

    golden = ROOT / "data/golden"
    cycles = [json.loads(line) for line in (golden / "cycles.jsonl").read_text().splitlines() if line.strip()]
    cgm = []
    for line in (golden / "cgm.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("source", "xdrip") != "xdrip":
                continue
            if args.uploader == "merged60":
                pass
            elif args.uploader:
                if args.uploader == "mixed":
                    cut = 1791419100000
                    wanted = "libre2" if row["ts"] < cut else "native"
                    if row.get("uploader") != wanted:
                        continue
                elif row.get("uploader") != args.uploader:
                    continue
            cgm.append((row["ts"], row["sgv"]))
    cgm.sort()
    if args.cgm == "db":
        cgm = [ (json.loads(line)["ts"], json.loads(line)["sgv"]) for line in (golden / "cgm_db.jsonl").read_text().splitlines() if line.strip() ]
    if args.uploader == "merged60" and args.cgm == "ns":
        merged = []
        for ts, sgv in cgm:
            if merged and ts - merged[-1][0] < 60_000:
                continue
            merged.append((ts, sgv))
        cgm = merged
    profiles = load_profiles(golden / "profiles.jsonl")
    boluses, temps = load_treatments(golden / "treatments.jsonl")
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    sensor = "Libre2"
    if not args.raw_fit and not args.auto_fit:
        prefs["always_promote_advanced_filtering"] = False
        sensor = "Unknown"
    bucketed_prefs = dict(prefs)
    bucketed_prefs["always_promote_advanced_filtering"] = False

    sensor_changes = sorted(
        json.loads(line)["ts"] for line in (golden / "treatments.jsonl").read_text().splitlines()
        if line.strip() and json.loads(line)["type"] == "Sensor Change"
    )

    from_time = None
    if args.from_time:
        from datetime import datetime, timezone
        from_time = int(datetime.fromisoformat(args.from_time).replace(tzinfo=timezone.utc).timestamp() * 1000)

    all_cycles = sorted((c for c in cycles if c["bg"] is not None), key=lambda c: c["ts"])
    candidates = []
    for cycle in all_cycles:
        if parse_parabola(cycle) is None:
            continue
        if args.version and cycle.get("appVersion") != args.version:
            continue
        if from_time and cycle["ts"] < from_time:
            continue
        candidates.append(cycle)
        if len(candidates) >= args.limit:
            break
    evaluate = {c["ts"] for c in candidates}

    glucose_errors, accel_errors, corr_errors, dura_errors = [], [], [], []
    glucose_exact = 0
    failures = 0
    path_counts = {"raw": 0, "bucketed": 0}
    timeline: list[tuple[int, float]] = []

    with Bridge() as bridge:
        bridge.request({
            "cmd": "init",
            "prefs": prefs,
            "profiles": profiles,
            "insulin": insulin,
            "treatments": boluses + temps,
            "timezone": "Europe/Moscow",
        })
        pending_resets = list(sensor_changes)
        for cycle in all_cycles:
            now = cycle["ts"]
            while pending_resets and pending_resets[0] <= now:
                pending_resets.pop(0)
                bridge.request({"cmd": "ukfReset"})
            if now not in evaluate:
                pending_resets = [t for t in pending_resets if t > now]
                response = bridge.request({"cmd": "glucose", "now": now, "readings": [[ts, sgv] for ts, sgv in cgm if now - 34 * 3600_000 <= ts <= now + 120_000], "sourceSensor": sensor})
                continue
            window = [[ts, sgv] for ts, sgv in cgm if now - 34 * 3600_000 <= ts <= now + 120_000]
            if args.auto_fit:
                bridge.request({"cmd": "pref", "key": "always_promote_advanced_filtering", "value": True})
            response = bridge.request({"cmd": "glucose", "now": now, "readings": window, "sourceSensor": sensor})
            status = response.get("glucose")
            path = "raw" if sensor == "Libre2" else "bucketed"
            if args.auto_fit:
                bridge.request({"cmd": "pref", "key": "always_promote_advanced_filtering", "value": False})
                bucketed = bridge.request({"cmd": "glucose", "now": now, "readings": window, "sourceSensor": "Unknown"})
                status_b = bucketed.get("glucose")
                expected0 = parse_parabola(cycle)
                if status_b is not None and expected0 is not None:
                    err_raw = abs(status["corrSqu"] - expected0[1]) + abs(status["parabolaMinutes"] - expected0[2]) / 60.0
                    err_b = abs(status_b["corrSqu"] - expected0[1]) + abs(status_b["parabolaMinutes"] - expected0[2]) / 60.0
                    if err_b < err_raw:
                        status = status_b
                        path = "bucketed"
                    else:
                        path = "raw"
                path_counts[path] += 1
            if status is None:
                failures += 1
                continue
            expected = parse_parabola(cycle)
            glucose_errors.append(abs(status["glucose"] - cycle["bg"]))
            timeline.append((now, abs(status["glucose"] - cycle["bg"])))
            if abs(status["glucose"] - cycle["bg"]) < 1e-9:
                glucose_exact += 1
            if expected:
                accel_errors.append(abs(status["bgAcceleration"] - expected[0]))
                corr_errors.append(abs(status["corrSqu"] - expected[1]))
                dura_errors.append(abs(status["parabolaMinutes"] - expected[2]))
            if args.verbose and len(glucose_errors) <= 10:
                print(json.dumps({
                    "ts": now, "recorded": cycle["bg"],
                    "glucose": status["glucose"], "delta": status["delta"],
                    "accel": status["bgAcceleration"], "expected_accel": expected[0] if expected else None,
                    "dura": status["parabolaMinutes"], "expected_dura": expected[2] if expected else None,
                }))

    print(f"cycles={len(candidates)} replayed={len(glucose_errors)} null={failures}")
    if args.timeline:
        from collections import defaultdict
        import datetime
        buckets: dict[str, list[float]] = defaultdict(list)
        for ts, err in timeline:
            day = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H")
            buckets[day].append(err)
        for day in sorted(buckets):
            values = buckets[day]
            print(f"  {day}  n={len(values):3d} exact={sum(1 for v in values if v == 0)}/{len(values)} p90={sorted(values)[int(len(values)*0.9)]:.2f}")
    print(f"glucose exact={glucose_exact}/{len(glucose_errors)}  {quantiles(glucose_errors)}")
    print(f"acceleration        {quantiles(accel_errors)}")
    print(f"correlation         {quantiles(corr_errors)}")
    print(f"parabolaMinutes     {quantiles(dura_errors)}")
    if args.auto_fit:
        print(f"path: {path_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
