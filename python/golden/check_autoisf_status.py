#!/usr/bin/env python3
"""Parity check: bridge autoISF status vs the Kotlin harness status CSV.

The CSV comes from `plugins/smoothing/build/reports/autoisf-rts-compare/status_*.csv`
(columns: pipeline, timestampMs, glucose, delta, shortAvgDelta, longAvgDelta, duraISFminutes,
duraISFaverage, parabolaMinutes, corrSqu, bgAcceleration, ...). The harness is the ground truth for
the Kotlin autoISF pipeline; this script feeds the same glucose series to the bridge and compares
the status fields the calculator returns.

NOTE: the harness computes the status from its own filtered `statusData` (it injects
`ads.currentStatusData` / `ads.currentReadings` and calls the calculator), while this script feeds
raw glucose values and lets the bridge re-run its own pipeline. The first run therefore shows 0 %
delta match: the next step is a bridge command that injects `statusData` the same way the harness
does.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

import numpy as np

from bridge.client import Bridge, insulin_from_settings, load_prefs
from cgm.validate_sensors import load_trace

FIELDS = (
    "delta", "shortAvgDelta", "longAvgDelta", "duraISFminutes", "duraISFaverage",
    "parabolaMinutes", "corrSqu", "bgAcceleration",
)


def load_block(path: Path, pipeline: str, limit: int) -> list[dict]:
    rows = [row for row in csv.DictReader(path.open(encoding="utf-8")) if row["pipeline"] == pipeline]
    rows.sort(key=lambda row: int(row["timestampMs"]))
    block: list[dict] = []
    for row in rows:
        ts = int(row["timestampMs"])
        if block and ts - int(block[-1]["timestampMs"]) != 5 * 60_000:
            break
        block.append(row)
        if len(block) >= limit:
            break
    return block


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--pipeline", default="raw_S0")
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--tolerance", type=float, default=0.01)
    ap.add_argument("--trace", help="trace CSV with raw readings (mmol/L) for the raw-fit path")
    ap.add_argument("--column", default="raw")
    args = ap.parse_args()

    block = load_block(Path(args.csv).expanduser(), args.pipeline, args.limit)
    if len(block) < 20:
        print(f"not enough contiguous rows for {args.pipeline}: {len(block)}")
        return 1
    readings = [[int(row["timestampMs"]), float(row["glucose"])] for row in block]
    trace_ts, trace_values = (None, None)
    if args.trace:
        trace_ts, trace_values = load_trace(Path(args.trace).expanduser(), args.column)
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")

    stats = {field: {"n": 0, "ok": 0, "max": 0.0} for field in FIELDS}
    with Bridge() as bridge:
        bridge.request({
            "cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin, "treatments": [],
            "basal": [[readings[0][0], 1.0]], "timezone": "Europe/Moscow",
        })
        for index in range(12, len(block)):
            row = block[index]
            now = int(row["timestampMs"])
            history = []
            for item in block[: index + 1]:
                ts = int(item["timestampMs"])
                smoothed = float(item["glucose"])
                raw = smoothed
                if trace_ts is not None:
                    near = int(np.argmin(np.abs(trace_ts - ts)))
                    if abs(int(trace_ts[near]) - ts) <= 60_000:
                        raw = float(trace_values[near])
                history.append({"ts": ts, "value": raw, "smoothed": smoothed, "filledGap": False})
            history = history[::-1]
            raw = None
            if trace_ts is not None:
                window = (trace_ts >= now - 6 * 60 * 60_000) & (trace_ts <= now)
                raw = [[int(t), float(v)] for t, v in zip(trace_ts[window], trace_values[window])]
            response = bridge.request({
                "cmd": "autoisfStatus", "now": now, "statusData": history, "readings": raw,
                "isf": 50.0, "carbRatio": 10.0, "target": 100.0, "currentBasal": 1.0,
            })
            status = response.get("status") or {}
            for field in FIELDS:
                expected = float(row[field])
                actual = status.get(field)
                if actual is None:
                    continue
                error = abs(float(actual) - expected)
                stats[field]["n"] += 1
                stats[field]["max"] = max(stats[field]["max"], error)
                if error <= args.tolerance * max(1.0, abs(expected)):
                    stats[field]["ok"] += 1

    print(f"pipeline {args.pipeline}, cycles {len(block) - 12}")
    for field in FIELDS:
        item = stats[field]
        rate = item["ok"] / item["n"] if item["n"] else 0.0
        print(f"  {field:18s} match {rate * 100:6.1f} %  max |err| {item['max']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
