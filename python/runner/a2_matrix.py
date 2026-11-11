#!/usr/bin/env python3
"""A2: paired autoISF on/off matrix across scenarios, with paired statistics."""
from __future__ import annotations

import argparse
import dataclasses
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
from exercise.model import ExerciseSession
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.metrics import compute_metrics
from runner.options import RESIDUALS, add_options, apply_prefs, load_cohort, load_residuals, noise_kwargs
from simcore.params import load_subject
from simcore.variability import si_day
from simcore.population import compute_cf, compute_cr, compute_isf_model, compute_tdi

PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")

METRIC_KEYS = [
    "tir_70_180", "tbr_70", "tbr_54", "lbgi", "smb_count", "total_insulin",
    "insulin_while_falling_fraction", "mean_glucose",
]

SCENARIOS = {
    "fcl-24h": dict(
        scenario=Scenario(
            name="fcl-24h", duration_min=24 * 60,
            meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)], meal_mode="fcl",
        ),
        profile_scale=1.0,
    ),
    "fcl-exercise": dict(
        scenario=Scenario(
            name="fcl-exercise", duration_min=12 * 60, meals=[(7 * 60, 50.0)], meal_mode="fcl",
            exercise=ExerciseSession(start_min=10 * 60, duration_min=45),
        ),
        profile_scale=1.0,
    ),
    "announced-8h": dict(
        scenario=Scenario(
            name="announced-8h", duration_min=8 * 60,
            meals=[(2 * 60, 50.0), (5 * 60, 50.0)], meal_mode="announced",
        ),
        profile_scale=1.0,
    ),
    "announced-error": dict(
        scenario=Scenario(
            name="announced-error", duration_min=8 * 60,
            meals=[(2 * 60, 50.0), (5 * 60, 50.0)], meal_mode="announced",
            announce_amount_error_frac=0.2, announce_time_error_min=15.0, announce_lead_min=10.0,
        ),
        profile_scale=1.0,
    ),
    "fcl-profile-error-strong": dict(
        scenario=Scenario(
            name="fcl-profile-error-strong", duration_min=24 * 60,
            meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)], meal_mode="fcl",
        ),
        profile_scale=0.5,
    ),
    "fcl-profile-error": dict(
        scenario=Scenario(
            name="fcl-profile-error", duration_min=24 * 60,
            meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)], meal_mode="fcl",
        ),
        profile_scale=0.7,
    ),
}


def profile_for(subject, auto_isf: bool, scale: float = 1.0, isf_source: str = "rule") -> AlgorithmProfile:
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    isf = compute_isf_model(subject) if isf_source == "model" else (compute_cf(tdi) or 50.0)
    return AlgorithmProfile(
        sens=isf * scale, carb_ratio=cr * scale, target_bg=110.0,
        max_iob=12.0, max_basal=4.0, current_basal=subject.basal_u_per_min * 60,
        enable_auto_isf=auto_isf,
    )


def run_task(task: dict) -> dict:
    subject = task["subject"]
    subject_name = subject.name
    scenario_name = task["scenario"]
    auto_isf = task["auto_isf"]
    temp_mode = task["temp_mode"]
    variability = task["variability"]
    isf_source = task["isf_source"]
    spec = SCENARIOS[scenario_name]
    scenario = spec["scenario"]
    seed = zlib.crc32(subject_name.encode()) & 0xFFFF
    if variability:
        scenario = dataclasses.replace(
            scenario, si_multipliers=si_day(seed).multipliers, dawn_egp=1.5,
        )
    profile = profile_for(subject, auto_isf, spec["profile_scale"], isf_source)
    residuals = load_residuals(task.get("residuals_path"))
    with Bridge() as bridge:
        init_bridge(bridge, subject, task["prefs"], INSULIN, profile, scenario)
        loop = run_closed_loop(
            subject, bridge, profile, scenario,
            seed=seed, residuals=residuals, temp_mode=temp_mode,
            **task["noise"],
        )
    metrics = compute_metrics(np.array(loop.cgm), loop.dose_events).as_dict()
    sens = np.array(loop.variable_sens, dtype=float)
    metrics["sens_ratio_mean"] = float(np.mean(sens) / profile.sens)
    metrics["sens_ratio_min"] = float(np.min(sens) / profile.sens)
    metrics["sens_ratio_max"] = float(np.max(sens) / profile.sens)
    metrics["sens_changed_frac"] = float(np.mean(np.abs(sens - profile.sens) > 1e-6))
    return {
        "subject": subject_name,
        "scenario": scenario_name,
        "auto_isf": auto_isf,
        "profile_scale": spec["profile_scale"],
        "temp_mode": temp_mode,
        "variability": variability,
        "isf_source": isf_source,
        "metrics": metrics,
    }


def paired_stats(rows: list[dict], key: str) -> dict:
    off = np.array([row["off"][key] for row in rows], dtype=float)
    on = np.array([row["on"][key] for row in rows], dtype=float)
    diff = on - off
    rng = np.random.default_rng(20261111)
    boot = rng.choice(diff, size=(10_000, diff.size), replace=True).mean(axis=1)
    try:
        from scipy.stats import wilcoxon

        p_value = float(wilcoxon(on, off).pvalue) if np.any(diff != 0) else 1.0
    except ValueError:
        p_value = 1.0
    return {
        "off_mean": float(off.mean()),
        "on_mean": float(on.mean()),
        "diff_mean": float(diff.mean()),
        "diff_median": float(np.median(diff)),
        "ci95_low": float(np.percentile(boot, 2.5)),
        "ci95_high": float(np.percentile(boot, 97.5)),
        "p_wilcoxon": p_value,
        "improved": int(np.sum(diff > 0)),
        "worsened": int(np.sum(diff < 0)),
        "n": int(diff.size),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", type=int, default=10)
    ap.add_argument("--scenarios", default="fcl-24h,fcl-exercise,announced-8h,fcl-profile-error")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--temp-mode", default="planned", choices=("planned", "cycle"))
    ap.add_argument("--variability", action="store_true")
    ap.add_argument("--isf-source", default="rule", choices=("rule", "model"))
    ap.add_argument("--out", default="out/a2_matrix.json")
    add_options(ap)
    args = ap.parse_args()

    cohort = load_cohort(args.cohort_copies, args.cohort_seed)
    names = [name for name, _ in sorted(cohort.items())][: args.subjects]
    prefs = apply_prefs(PREFS, args.pref)
    noise = noise_kwargs(args)
    scenarios = [s for s in args.scenarios.split(",") if s]
    tasks = [
        {
            "subject": cohort[name], "scenario": scenario, "auto_isf": auto_isf,
            "temp_mode": args.temp_mode, "variability": args.variability,
            "isf_source": args.isf_source, "prefs": prefs, "noise": noise,
            "residuals_path": args.noise_file,
        }
        for scenario in scenarios for name in names for auto_isf in (False, True)
    ]

    started = time.time()
    with mp.Pool(processes=args.workers) as pool:
        results = pool.map(run_task, tasks, chunksize=1)

    by_key = {(row["subject"], row["scenario"], row["auto_isf"]): row for row in results}
    report = {}
    for scenario in scenarios:
        rows = []
        for name in names:
            off = by_key[(name, scenario, False)]
            on = by_key[(name, scenario, True)]
            rows.append({
                "subject": name,
                "off": off["metrics"],
                "on": on["metrics"],
                "profile_scale": off["profile_scale"],
            })
        report[scenario] = {
            "profile_scale": rows[0]["profile_scale"],
            "n": len(rows),
            "stats": {key: paired_stats(rows, key) for key in METRIC_KEYS},
            "rows": rows,
        }

    payload = {
        "elapsed_s": round(time.time() - started, 1), "temp_mode": args.temp_mode,
        "variability": args.variability, "isf_source": args.isf_source,
        "cohort_copies": args.cohort_copies, "cohort_seed": args.cohort_seed,
        "noise": args.noise, "noise_scale": args.noise_scale, "prefs": args.pref,
        "subjects": names, "report": report,
    }
    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for scenario, block in report.items():
        print(f"== {scenario} (n={block['n']}, profile_scale={block['profile_scale']})")
        for key in ("tir_70_180", "tbr_70", "lbgi", "smb_count", "total_insulin", "insulin_while_falling_fraction"):
            st = block["stats"][key]
            print(
                f"{key:30s} off={st['off_mean']:.3f} on={st['on_mean']:.3f} "
                f"diff={st['diff_mean']:+.3f} CI95=[{st['ci95_low']:+.3f},{st['ci95_high']:+.3f}] "
                f"p={st['p_wilcoxon']:.4f} better={st['improved']}/{st['n']}"
            )
    print(f"written {out} ({payload['elapsed_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
