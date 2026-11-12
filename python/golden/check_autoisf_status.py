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
    ap.add_argument("--readings", help="harness readings CSV (timestampMs,value in mg/dL)")
    ap.add_argument("--readings-from-status", action="store_true",
                    help="use the pipeline's own status series as readings (smoothed/grid pipelines)")
    ap.add_argument("--status-points",
                    help="harness statuspoints CSV: the full statusData per decision point")
    ap.add_argument("--readings-points",
                    help="harness readingspoints CSV: the exact readings per decision point")
    ap.add_argument("--column", default="raw")
    ap.add_argument("--window-min", type=int, default=0,
                    help="limit readings to this many minutes before each decision point (harness window)")
    args = ap.parse_args()

    block = load_block(Path(args.csv).expanduser(), args.pipeline, args.limit)
    if len(block) < 20:
        print(f"not enough contiguous rows for {args.pipeline}: {len(block)}")
        return 1
    readings = [[int(row["timestampMs"]), float(row["glucose"])] for row in block]
    trace_ts, trace_values = (None, None)
    if args.readings:
        data = np.loadtxt(Path(args.readings).expanduser(), delimiter=",", skiprows=1)
        trace_ts, trace_values = data[:, 0].astype(np.int64), data[:, 1]
    elif args.trace:
        trace_ts, trace_values = load_trace(Path(args.trace).expanduser(), args.column)
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")

    points: dict[int, list[dict]] = {}
    if args.status_points:
        with Path(args.status_points).expanduser().open(encoding="utf-8") as handle:
            for item in csv.DictReader(handle):
                points.setdefault(int(item["decisionTs"]), []).append({
                    "ts": int(item["pointTs"]),
                    "value": float(item["value"]),
                    "smoothed": float(item["smoothed"]),
                    "filledGap": str(item["filledGap"]).lower() == "true",
                })

    readings_points: dict[int, list[list[float]]] = {}
    if args.readings_points:
        with Path(args.readings_points).expanduser().open(encoding="utf-8") as handle:
            for item in csv.DictReader(handle):
                readings_points.setdefault(int(item["decisionTs"]), []).append(
                    [int(item["ts"]), float(item["value"])]
                )

    stats = {field: {"n": 0, "ok": 0, "max": 0.0} for field in FIELDS}
    with Bridge() as bridge:
        bridge.request({
            "cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin, "treatments": [],
            "basal": [[readings[0][0], 1.0]], "timezone": "Europe/Moscow",
        })
        for index in range(12, len(block)):
            row = block[index]
            now = int(row["timestampMs"])
            if points:
                if now not in points:
                    continue
                history = sorted(points[now], key=lambda item: item["ts"], reverse=True)
            else:
                history = []
                for item in block[: index + 1]:
                    ts = int(item["timestampMs"])
                    smoothed = float(item.get("smoothed") or item["glucose"])
                    raw = float(item.get("value") or smoothed)
                    gap = str(item.get("filledGap", "false")).lower() == "true"
                    if trace_ts is not None and "value" not in item:
                        near = int(np.argmin(np.abs(trace_ts - ts)))
                        if abs(int(trace_ts[near]) - ts) <= 60_000:
                            raw = float(trace_values[near])
                    history.append({"ts": ts, "value": raw, "smoothed": smoothed, "filledGap": gap})
                history = history[::-1]
            raw = None
            if readings_points:
                raw = readings_points.get(now)
            elif args.readings_from_status:
                raw = [[int(item["timestampMs"]), float(item["glucose"])] for item in block[: index + 1]]
            elif trace_ts is not None:
                span = (args.window_min or 360) * 60_000
                window = (trace_ts >= now - span) & (trace_ts <= now)
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

    compared = stats["delta"]["n"]
    print(f"pipeline {args.pipeline}, cycles {compared}")
    for field in FIELDS:
        item = stats[field]
        rate = item["ok"] / item["n"] if item["n"] else 0.0
        print(f"  {field:18s} match {rate * 100:6.1f} %  max |err| {item['max']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
