#!/usr/bin/env python3
"""Compare OpenAPSAutoISFPlugin.autoISF() with the ISF factor the app logged."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.client import Bridge, load_prefs, load_profiles, load_treatments, insulin_from_settings

ROOT = Path(__file__).resolve().parents[2]
SENS = re.compile(r"profile\.sens: ([\d.]+), sens: ([\d.]+), baseCR: ([\d.]+), effectiveCR: ([\d.]+)")
TARGET = re.compile(r"current target ([\d.]+)")
ISF_UNCHANGED = re.compile(r"ISF unchanged: ([\d.]+)")


def parse(text: str) -> dict | None:
    match = SENS.search(text)
    if not match:
        return None
    target = TARGET.search(text)
    unchanged = ISF_UNCHANGED.search(text)
    return {
        "profileSens": float(match.group(1)),
        "sens": float(match.group(2)),
        "baseCr": float(match.group(3)),
        "target": float(target.group(1)) if target else None,
        "isf": float(unchanged.group(1)) if unchanged else None,
    }


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
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--from-time", default=None)
    ap.add_argument("--version", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    golden = ROOT / "data/golden"
    cycles = [json.loads(line) for line in (golden / "cycles.jsonl").read_text().splitlines() if line.strip()]
    cgm = []
    for line in (golden / "cgm.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("source", "xdrip") != "xdrip" or row.get("uploader") != "libre2":
            continue
        cgm.append((row["ts"], row["sgv"]))
    cgm.sort()
    profiles = load_profiles(golden / "profiles.jsonl") if False else []
    boluses, temps = load_treatments(golden / "treatments.jsonl")
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    prefs["always_promote_advanced_filtering"] = True
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")

    from datetime import datetime, timezone
    from_time = None
    if args.from_time:
        from_time = int(datetime.fromisoformat(args.from_time).replace(tzinfo=timezone.utc).timestamp() * 1000)

    candidates = []
    for cycle in cycles:
        text = " ".join(cycle.get("consoleError") or [])
        parsed = parse(text)
        if parsed is None or parsed["target"] is None:
            continue
        if args.version and cycle.get("appVersion") != args.version:
            continue
        if from_time and cycle["ts"] < from_time:
            continue
        cycle["_parsed"] = parsed
        candidates.append(cycle)
        if len(candidates) >= args.limit:
            break

    errors = []
    with Bridge() as bridge:
        bridge.request({
            "cmd": "init",
            "prefs": prefs,
            "profiles": profiles,
            "insulin": insulin,
            "treatments": boluses + temps,
            "basal": [[c["ts"], c["baseBasal"]] for c in cycles if c.get("baseBasal") is not None],
            "timezone": "Europe/Moscow",
        })
        for cycle in candidates:
            parsed = cycle["_parsed"]
            target_mgdl = parsed["target"] * 18.0
            window = [[ts, sgv] for ts, sgv in cgm if cycle["ts"] - 34 * 3600_000 <= ts <= cycle["ts"] + 120_000]
            response = bridge.request({
                "cmd": "autoisf",
                "now": cycle["ts"],
                "readings": window,
                "sourceSensor": "Libre2",
                "isf": parsed["isf"] or parsed["profileSens"],
                "carbRatio": parsed["baseCr"],
                "target": target_mgdl,
                "targetLow": target_mgdl,
                "targetHigh": target_mgdl,
                "currentBasal": cycle.get("baseBasal") or 0.0,
            })
            errors.append(abs(response["sens"] - parsed["sens"]))
            if args.verbose and len(errors) <= 10:
                print(json.dumps({"ts": cycle["ts"], "expected": parsed["sens"], "got": response["sens"],
                                  "isf": parsed["isf"], "target": round(target_mgdl, 1)}))

    print(f"cycles={len(candidates)}")
    print(f"ISF abs error  {quantiles(errors)}")
    print(f"exact={sum(1 for e in errors if e < 1e-9)}/{len(errors)}  <=0.1={sum(1 for e in errors if e <= 0.1)}/{len(errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
