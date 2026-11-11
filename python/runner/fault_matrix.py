#!/usr/bin/env python3
"""Fault catalogue: bolus delay, CGM gap, CGM bias, pump suspend against a clean baseline."""
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
from pump.pump import PumpConfig
from runner.a2_matrix import paired_stats
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.metrics import compute_metrics
from runner.options import RESIDUALS, add_options, apply_prefs, load_cohort, noise_kwargs
from simcore.population import compute_cf, compute_cr, compute_tdi

PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
MEALS = [(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)]
METRICS = ("tir_70_180", "tbr_70", "lbgi", "smb_count", "total_insulin")

FAULTS: dict[str, dict] = {
    "baseline": {"scenario": Scenario(duration_min=24 * 60, meals=MEALS), "pump": None},
    "bolus-delay-15": {
        "scenario": Scenario(duration_min=24 * 60, meals=MEALS),
        "pump": PumpConfig(bolus_delay_min=15.0),
    },
    "cgm-gap-60": {"scenario": Scenario(duration_min=24 * 60, meals=MEALS, cgm_gap=(600.0, 60.0)), "pump": None},
    "cgm-bias-plus50-2h": {
        "scenario": Scenario(duration_min=24 * 60, meals=MEALS, cgm_bias=(600.0, 120.0, 50.0)),
        "pump": None,
    },
    "suspend-60": {"scenario": Scenario(duration_min=24 * 60, meals=MEALS, suspend=(450.0, 60.0)), "pump": None},
}


def profile_for(subject, auto_isf: bool = False) -> AlgorithmProfile:
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    return AlgorithmProfile(
        sens=compute_cf(tdi) or 50.0, carb_ratio=cr, target_bg=110.0,
        max_iob=12.0, max_basal=4.0, current_basal=subject.basal_u_per_min * 60,
        enable_auto_isf=auto_isf,
    )


def run_task(task: dict) -> dict:
    subject = task["subject"]
    subject_name = subject.name
    fault = task["fault"]
    auto_isf = task["auto_isf"]
    spec = FAULTS[fault]
    profile = profile_for(subject, auto_isf)
    seed = zlib.crc32(subject_name.encode()) & 0xFFFF
    with Bridge() as bridge:
        init_bridge(bridge, subject, task["prefs"], INSULIN, profile, spec["scenario"])
        loop = run_closed_loop(
            subject, bridge, profile, spec["scenario"], seed=seed, residuals=RESIDUALS,
            pump_config=spec["pump"], **task["noise"],
        )
    metrics = compute_metrics(np.array(loop.cgm), loop.dose_events).as_dict()
    return {"subject": subject_name, "fault": fault, "auto_isf": auto_isf, "metrics": metrics}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", type=int, default=10)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--compare", action="store_true", help="run autoISF off/on and report paired deltas")
    ap.add_argument("--out", default="out/fault_matrix.json")
    add_options(ap)
    args = ap.parse_args()

    cohort = load_cohort(args.cohort_copies, args.cohort_seed)
    names = [name for name, _ in sorted(cohort.items())][: args.subjects]
    prefs = apply_prefs(PREFS, args.pref)
    noise = noise_kwargs(args)
    arms = (False, True) if args.compare else (False,)
    tasks = [
        {"subject": cohort[name], "fault": fault, "auto_isf": auto_isf, "prefs": prefs, "noise": noise}
        for fault in FAULTS for name in names for auto_isf in arms
    ]
    started = time.time()
    with mp.Pool(processes=args.workers) as pool:
        results = pool.map(run_task, tasks, chunksize=1)

    by_key = {(row["subject"], row["fault"], row["auto_isf"]): row["metrics"] for row in results}
    report = {}
    for fault in FAULTS:
        rows = [by_key[(name, fault, False)] for name in names]
        report[fault] = {
            key: float(np.mean([row[key] for row in rows])) for key in METRICS
        }
        report[fault]["n"] = len(rows)
        report[fault]["delta_tir_vs_baseline"] = float(
            np.mean([by_key[(name, fault, False)]["tir_70_180"] - by_key[(name, "baseline", False)]["tir_70_180"] for name in names])
        )
        if args.compare:
            paired = [
                {"off": by_key[(name, fault, False)], "on": by_key[(name, fault, True)]}
                for name in names
            ]
            report[fault]["auto_isf"] = {
                key: paired_stats(paired, key)
                for key in ("tir_70_180", "tbr_70", "lbgi", "smb_count", "total_insulin", "insulin_while_falling_fraction")
            }

    payload = {
        "elapsed_s": round(time.time() - started, 1), "subjects": names,
        "compare": args.compare, "report": report,
    }
    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"{'fault':22s} {'TIR':>6s} {'dTIR':>7s} {'TBR':>6s} {'LBGI':>6s} {'SMB':>5s} {'dose':>6s}")
    for fault, stats in report.items():
        print(
            f"{fault:22s} {stats['tir_70_180']:6.3f} {stats['delta_tir_vs_baseline']:+7.3f} "
            f"{stats['tbr_70']:6.3f} {stats['lbgi']:6.2f} {stats['smb_count']:5.0f} {stats['total_insulin']:6.1f}"
        )
    if args.compare:
        print("\n== autoISF on/off under each fault (paired)")
        for fault, stats in report.items():
            block = stats["auto_isf"]
            print(f"-- {fault}")
            for key in ("tir_70_180", "tbr_70", "lbgi", "total_insulin", "insulin_while_falling_fraction"):
                st = block[key]
                print(
                    f"   {key:30s} off={st['off_mean']:.3f} on={st['on_mean']:.3f} "
                    f"diff={st['diff_mean']:+.3f} CI95=[{st['ci95_low']:+.3f},{st['ci95_high']:+.3f}] "
                    f"p={st['p_wilcoxon']:.4f} higher={st['improved']}/{st['n']}"
                )
    print(f"written {out} ({payload['elapsed_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
