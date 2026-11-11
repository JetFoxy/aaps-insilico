#!/usr/bin/env python3
"""Sensor-noise statistics on the public MetaboNet parquet, next to the shipped models."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from cgm.metabonet import load_trace, subject_ids
from cgm.validate_sensors import model_residuals, residuals, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("parquet")
    ap.add_argument("--subjects", type=int, default=3)
    ap.add_argument("--row-groups", type=int, default=1)
    args = ap.parse_args()

    ids = subject_ids(args.parquet, args.row_groups)[: args.subjects]
    print(f"{'subject':10s} {'device':22s} {'n':>6s} {'sigma':>7s} {'lag1':>6s} {'slope':>6s}")
    all_res = []
    for subject in ids:
        timestamps, values, device = load_trace(args.parquet, subject, args.row_groups)
        res = residuals(timestamps, values)
        st = stats(res, 1.0 / 5.0)
        all_res.append(res)
        print(f"{subject:10s} {device:22s} {st['n']:6d} {st['sigma']:7.2f} {st['lag1']:6.2f} {st['slope']:6.2f}")

    import numpy as np

    pooled = np.concatenate(all_res)
    n = pooled.size
    print("\nmodels on the same length:")
    for kind in ("replay", "facchinetti", "dexcom"):
        model = model_residuals(kind, n, seed=1, residuals_source=pooled, fs=1.0 / 5.0)
        st = stats(model, 1.0 / 5.0)
        print(f"  {kind:14s} {st['n']:6d} {st['sigma']:7.2f} {st['lag1']:6.2f} {st['slope']:6.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
