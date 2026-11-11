#!/usr/bin/env python3
"""Diagnostic: how much of the baseline TIR loss comes from SI classes vs dawn."""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
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
from simcore.params import load_subject, load_subjects
from simcore.population import compute_cf, compute_cr, compute_isf_model, compute_tdi
from simcore.variability import si_day

PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
RESIDUALS = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")

VARIANTS = ("plain", "si", "dawn", "both")


def run_task(task: tuple[str, str, str]) -> dict:
    subject_name, variant, isf_source = task
    seed = zlib.crc32(subject_name.encode()) & 0xFFFF
    subject = load_subject(subject_name)
    si = si_day(seed).multipliers if variant in ("si", "both") else None
    dawn = 1.5 if variant in ("dawn", "both") else 0.0
    scenario = Scenario(
        name=f"fcl-24h-{variant}", duration_min=24 * 60,
        meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)], meal_mode="fcl",
        si_multipliers=si, dawn_egp=dawn,
    )
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    isf = compute_isf_model(subject) if isf_source == "model" else (compute_cf(tdi) or 50.0)
    profile = AlgorithmProfile(
        sens=isf, carb_ratio=cr, target_bg=110.0,
        max_iob=12.0, max_basal=4.0, current_basal=subject.basal_u_per_min * 60,
    )
    with Bridge() as bridge:
        init_bridge(bridge, subject, PREFS, INSULIN, profile, scenario)
        loop = run_closed_loop(
            subject, bridge, profile, scenario, seed=seed, residuals=RESIDUALS,
        )
    metrics = compute_metrics(np.array(loop.cgm), loop.dose_events).as_dict()
    return {
        "subject": subject_name, "variant": variant,
        "si_class": si_day(seed).name, "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--isf-source", default="rule", choices=("rule", "model"))
    parser.add_argument("--subjects", type=int, default=10)
    args = parser.parse_args()
    subjects = [name for name, _ in sorted(load_subjects().items())][: args.subjects]
    tasks = [(name, variant, args.isf_source) for variant in VARIANTS for name in subjects]
    started = time.time()
    with mp.Pool(processes=6) as pool:
        results = pool.map(run_task, tasks, chunksize=1)
    table = {}
    for variant in VARIANTS:
        rows = [row for row in results if row["variant"] == variant]
        table[variant] = {
            key: float(np.mean([row["metrics"][key] for row in rows]))
            for key in ("tir_70_180", "tbr_70", "mean_glucose", "total_insulin", "smb_count")
        }
        table[variant]["n"] = len(rows)
    payload = {
        "elapsed_s": round(time.time() - started, 1), "subjects": subjects,
        "isf_source": args.isf_source, "table": table,
    }
    out = ROOT / "out" / f"diag_variability_{args.isf_source}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"{'variant':8s} {'TIR':>6s} {'TBR':>6s} {'meanBG':>7s} {'dose':>7s} {'SMB':>5s}")
    for variant, stats in table.items():
        print(
            f"{variant:8s} {stats['tir_70_180']:6.3f} {stats['tbr_70']:6.3f} "
            f"{stats['mean_glucose']:7.1f} {stats['total_insulin']:7.1f} {stats['smb_count']:5.0f}"
        )
    print(f"written {out} ({payload['elapsed_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
