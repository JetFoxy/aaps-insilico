#!/usr/bin/env python3
"""M7 matrix: run subjects through scenarios and write a JSON + markdown summary."""
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
from exercise.model import ExerciseSession
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.metrics import compute_metrics
from simcore.params import load_subjects
from simcore.population import compute_cf, compute_cr, compute_tdi

PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
RESIDUALS = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")


def profile_for(subject, target: float = 110.0) -> AlgorithmProfile:
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    isf = compute_cf(tdi) or 50.0
    return AlgorithmProfile(
        sens=isf, carb_ratio=cr, target_bg=target,
        max_iob=12.0, max_basal=4.0, current_basal=subject.basal_u_per_min * 60,
    )


SCENARIOS = {
    "fcl-3meals": Scenario(name="fcl-3meals", duration_min=24 * 60, meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)], meal_mode="fcl"),
    "fcl-8h": Scenario(name="fcl-8h", duration_min=8 * 60, meals=[(2 * 60, 50.0), (5 * 60, 50.0)], meal_mode="fcl"),
    "announced-3meals": Scenario(name="announced-3meals", duration_min=8 * 60, meals=[(2 * 60, 50.0), (5 * 60, 50.0)], meal_mode="announced"),
    "fcl-exercise": Scenario(name="fcl-exercise", duration_min=12 * 60, meals=[(7 * 60, 50.0)], meal_mode="fcl", exercise=ExerciseSession(start_min=10 * 60, duration_min=45)),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--out", default="out/m7_matrix.json")
    args = ap.parse_args()

    subjects = [s for name, s in sorted(load_subjects().items())][: args.limit]
    results = []
    started = time.time()
    for scenario_name, scenario in SCENARIOS.items():
        if scenario_name in ("announced-3meals", "fcl-8h"):
            cohort = subjects[:3]
        elif scenario_name == "fcl-exercise":
            cohort = subjects[:3]
        else:
            cohort = subjects
        for subject in cohort:
            profile = profile_for(subject)
            with Bridge() as bridge:
                init_bridge(bridge, subject, PREFS, INSULIN, profile, scenario)
                loop = run_closed_loop(subject, bridge, profile, scenario, seed=zlib.crc32(subject.name.encode()) & 0xFFFF, residuals=RESIDUALS)
            metrics = compute_metrics(np.array(loop.cgm), loop.dose_events).as_dict()
            results.append({
                "subject": subject.name,
                "scenario": scenario_name,
                "cr": profile.carb_ratio,
                "isf": profile.sens,
                "metrics": metrics,
            })
            print(f"{scenario_name:18s} {subject.name:16s} TIR={metrics['tir_70_180']:.2f} TBR={metrics['tbr_70']:.2f} LBGI={metrics['lbgi']:.2f} SMB={metrics['smb_count']}", flush=True)

    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    aggregates = {}
    for scenario_name in SCENARIOS:
        rows = [row["metrics"] for row in results if row["scenario"] == scenario_name]
        if rows:
            aggregates[scenario_name] = {
                key: float(np.mean([row[key] for row in rows])) for key in rows[0]
            }
            aggregates[scenario_name]["runs"] = len(rows)
    payload = {"elapsed_s": round(time.time() - started, 1), "aggregates": aggregates, "results": results}
    for scenario_name, stats in aggregates.items():
        print(f"== {scenario_name}: runs={stats['runs']:.0f} TIR={stats['tir_70_180']:.2f} TBR={stats['tbr_70']:.2f} "
              f"LBGI={stats['lbgi']:.2f} SMB={stats['smb_count']:.0f} dose={stats['total_insulin']:.1f}")
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"written {out} ({len(results)} runs, {payload['elapsed_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
