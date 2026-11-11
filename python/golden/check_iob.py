#!/usr/bin/env python3
"""Compare the replayed IOB array at t=now with the IOB the app recorded."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.client import Bridge, load_prefs, load_profiles, load_treatments, insulin_from_settings

ROOT = Path(__file__).resolve().parents[2]


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
    ap.add_argument("--from-time", default=None)
    ap.add_argument("--version", default=None)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--offset", type=int, default=0, help="milliseconds added to the cycle time")
    args = ap.parse_args()

    golden = ROOT / "data/golden"
    cycles = [json.loads(line) for line in (golden / "cycles.jsonl").read_text().splitlines() if line.strip()]
    profiles = load_profiles(golden / "profiles.jsonl")
    boluses, temps = load_treatments(golden / "treatments.jsonl")
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")

    from datetime import datetime, timezone
    from_time = None
    if args.from_time:
        from_time = int(datetime.fromisoformat(args.from_time).replace(tzinfo=timezone.utc).timestamp() * 1000)

    candidates = [c for c in cycles if c["iob"] is not None]
    if args.version:
        candidates = [c for c in candidates if c.get("appVersion") == args.version]
    if from_time:
        candidates = [c for c in candidates if c["ts"] >= from_time]
    candidates = candidates[: args.limit]

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
            "bolusSnoozeDivisor": float(prefs.get("bolussnooze_dia_divisor") or 1.5),
        })
        for cycle in candidates:
            response = bridge.request({
                "cmd": "iob",
                "now": cycle["ts"] + args.offset,
                "autosens": cycle.get("sensRatio") or 1.0,
                "exerciseMode": False,
                "halfBasalExerciseTarget": 160,
                "isTempTarget": False,
            })
            iob = response["iob"][0]["iob"]
            errors.append(abs(iob - cycle["iob"]))
            if args.verbose and len(errors) <= 12:
                print(json.dumps({"ts": cycle["ts"], "recorded": cycle["iob"], "replayed": iob,
                                  "cob": cycle["cob"], "delta": round(iob - cycle["iob"], 3)}))

    print(f"cycles={len(candidates)}")
    print(f"IOB abs error  {quantiles(errors)}")
    exact = sum(1 for e in errors if e < 1e-9)
    print(f"exact={exact}/{len(errors)}  <=0.01={sum(1 for e in errors if e <= 0.01)}/{len(errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
