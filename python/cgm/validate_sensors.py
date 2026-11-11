#!/usr/bin/env python3
"""Compare sensor-error models against real CGM traces.

Reads a trace CSV (timestampMs, raw in mmol/L), computes residuals against a Savitzky-Golay
trend, and compares sigma / lag-1 / PSD slope with the published models and the real-residual
replay. Optionally reports MARD against a fingerstick CSV (timestampMs, mgdl).

The traces are personal CGM exports and are never committed; only aggregate statistics are printed.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from scipy.signal import savgol_filter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from cgm.noise import AR1Noise, BlockNoise
from cgm.residuals import SG_POLY, SG_WINDOW, _segments, psd_slope
from cgm.sensors import make_sensor

MMOL_TO_MGDL = 18.018


def load_trace(path: Path, column: str = "calibrated") -> tuple[np.ndarray, np.ndarray]:
    timestamps, values = [], []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                ts = int(row["timestampMs"])
                value = float(row[column])
            except (KeyError, TypeError, ValueError):
                continue
            if value > 0:
                timestamps.append(ts)
                values.append(value * MMOL_TO_MGDL)
    return np.array(timestamps, dtype=np.int64), np.array(values, dtype=float)


def resample_5min(timestamps: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if timestamps.size < 3:
        return timestamps, values
    step_ms = float(np.median(np.diff(timestamps)))
    factor = max(1, int(round(5 * 60_000 / step_ms))) if step_ms > 0 else 1
    return timestamps[::factor], values[::factor]


def residuals(timestamps: np.ndarray, values: np.ndarray) -> np.ndarray:
    parts = []
    for _, segment in _segments(timestamps, values):
        if segment.size >= SG_WINDOW:
            parts.append(segment - savgol_filter(segment, SG_WINDOW, SG_POLY, mode="interp"))
    return np.concatenate(parts) if parts else np.array([])


def stats(values: np.ndarray, fs: float) -> dict:
    if values.size < 3:
        return {"n": values.size, "sigma": float("nan"), "lag1": float("nan"), "slope": float("nan")}
    return {
        "n": int(values.size),
        "sigma": float(values.std(ddof=1)),
        "lag1": float(np.corrcoef(values[:-1], values[1:])[0, 1]),
        "slope": psd_slope(values, fs),
    }


def model_residuals(kind: str, n: int, seed: int, residuals_source: np.ndarray, fs: float) -> np.ndarray:
    if kind == "replay":
        return BlockNoise(residuals_source, seed=seed).sample(n)
    if kind == "ar1":
        return AR1Noise(0.8, 2.49, seed=seed).sample(n)
    sensor = make_sensor(kind, seed)
    cgm = np.array([sensor.measure(120.0, t) for t in range(1, n + 1)])
    return cgm - savgol_filter(cgm, SG_WINDOW, SG_POLY, mode="interp")


def mard(trace_ts: np.ndarray, trace_values: np.ndarray, finger_path: Path) -> float:
    pairs = []
    with finger_path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                ts = int(row["timestampMs"])
                mgdl = float(row["mgdl"])
            except (KeyError, TypeError, ValueError):
                continue
            index = int(np.argmin(np.abs(trace_ts - ts)))
            if abs(int(trace_ts[index]) - ts) <= 5 * 60_000:
                pairs.append(abs(trace_values[index] - mgdl) / mgdl)
    return float(np.mean(pairs) * 100) if pairs else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("traces", nargs="+", help="trace CSVs (timestampMs, raw in mmol/L)")
    ap.add_argument("--column", default="calibrated")
    args = ap.parse_args()

    print(f"{'trace':28s} {'n':>6s} {'sigma':>7s} {'lag1':>6s} {'slope':>6s} {'MARD%':>6s}")
    for trace in args.traces:
        path = Path(trace).expanduser()
        timestamps, values = load_trace(path, args.column)
        timestamps, values = resample_5min(timestamps, values)
        res = residuals(timestamps, values)
        fs = 1.0 / 5.0
        st = stats(res, fs)
        finger = path.with_name(path.stem + "_fingersticks.csv")
        md = mard(timestamps, values, finger) if finger.exists() else float("nan")
        print(f"{path.name:28s} {st['n']:6d} {st['sigma']:7.2f} {st['lag1']:6.2f} {st['slope']:6.2f} {md:6.1f}")

        n = st["n"]
        for kind in ("replay", "ar1", "facchinetti", "dexcom"):
            model = model_residuals(kind, n, seed=1, residuals_source=res, fs=fs)
            ms = stats(model, fs)
            print(f"  {kind:26s} {ms['n']:6d} {ms['sigma']:7.2f} {ms['lag1']:6.2f} {ms['slope']:6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
