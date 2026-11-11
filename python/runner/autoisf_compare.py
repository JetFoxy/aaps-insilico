#!/usr/bin/env python3
"""Compare autoISF on/off in the closed loop on the same subjects and scenario."""
from __future__ import annotations

import argparse
import json
import sys
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from bridge.client import Bridge, insulin_from_settings, load_prefs
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.metrics import compute_metrics
from simcore.params import load_subjects
from simcore.population import compute_cf, compute_cr, compute_tdi

PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
RESIDUALS = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")
SCENARIO = Scenario(name="fcl-3meals", duration_min=24 * 60, meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)], meal_mode="fcl")


def profile_for(subject, auto_isf: bool) -> AlgorithmProfile:
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    isf = compute_cf(tdi) or 50.0
    return AlgorithmProfile(
        sens=isf, carb_ratio=cr, target_bg=110.0,
        max_iob=12.0, max_basal=4.0, current_basal=subject.basal_u_per_min * 60,
        enable_auto_isf=auto_isf,
    )


def run_one(subject, auto_isf: bool) -> dict:
    profile = profile_for(subject, auto_isf)
    with Bridge() as bridge:
        init_bridge(bridge, subject, PREFS, INSULIN, profile, SCENARIO)
        loop = run_closed_loop(
            subject, bridge, profile, SCENARIO,
            seed=zlib.crc32(subject.name.encode()) & 0xFFFF, residuals=RESIDUALS,
        )
    return compute_metrics(np.array(loop.cgm), loop.dose_events).as_dict()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--out", default="out/autoisf_compare.json")
    args = ap.parse_args()
    subjects = [s for name, s in sorted(load_subjects().items())][: args.limit]
    started = time.time()
    rows = []
    for subject in subjects:
        off = run_one(subject, False)
        on = run_one(subject, True)
        rows.append({"subject": subject.name, "off": off, "on": on})
        print(
            f"{subject.name:16s} TIR {off['tir_70_180']:.2f}->{on['tir_70_180']:.2f} "
            f"TBR {off['tbr_70']:.2f}->{on['tbr_70']:.2f} "
            f"SMB {off['smb_count']}->{on['smb_count']} dose {off['total_insulin']:.1f}->{on['total_insulin']:.1f}",
            flush=True,
        )
    keys = ["tir_70_180", "tbr_70", "tbr_54", "lbgi", "smb_count", "total_insulin", "insulin_while_falling_fraction"]
    aggregates = {
        key: {
            "off": float(np.mean([row["off"][key] for row in rows])),
            "on": float(np.mean([row["on"][key] for row in rows])),
        }
        for key in keys
    }
    payload = {"elapsed_s": round(time.time() - started, 1), "aggregates": aggregates, "rows": rows}
    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("== aggregates")
    for key, value in aggregates.items():
        print(f"{key:32s} off={value['off']:.3f} on={value['on']:.3f}")
    print(f"written {out} ({payload['elapsed_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
