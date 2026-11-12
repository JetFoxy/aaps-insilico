#!/usr/bin/env python3
"""Export precomputed closed-loop runs for the static demo UI (web/data/runs.js)."""
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
from pump.pump import PumpConfig
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.metrics import compute_metrics
from runner.options import add_options, apply_prefs, noise_kwargs
from simcore.params import load_subject
from simcore.population import cached_subject_profile

PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
RESIDUALS = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")
MEALS = [(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)]


def build_scenario(name: str) -> tuple[Scenario, PumpConfig | None]:
    if name == "fcl-24h":
        return Scenario(name=name, duration_min=24 * 60, meals=MEALS), None
    if name == "fcl-exercise":
        return Scenario(
            name=name, duration_min=24 * 60, meals=MEALS,
            exercise=ExerciseSession(start_min=10 * 60, duration_min=45),
        ), None
    if name == "announced-8h":
        return Scenario(
            name=name, duration_min=8 * 60, meals=[(2 * 60, 50.0), (5 * 60, 50.0)], meal_mode="announced",
        ), None
    if name == "fault-delay":
        return Scenario(name=name, duration_min=24 * 60, meals=MEALS), PumpConfig(bolus_delay_min=15.0)
    if name == "cgm-bias":
        return Scenario(name=name, duration_min=24 * 60, meals=MEALS, cgm_bias=(600.0, 120.0, 50.0)), None
    raise ValueError(f"unknown scenario {name}")


def profile_for(subject, auto_isf: bool, isf_source: str = "rule", smb_ratio_3min: float = 0.3, algorithm: str = "autoisf") -> AlgorithmProfile:
    cached = cached_subject_profile(subject, isf_source)
    cr = cached["cr"] or 10.5
    tdi = cached["tdi"] or 40.0
    isf = cached["isf"] or 50.0
    return AlgorithmProfile(
        sens=isf, carb_ratio=cr, target_bg=110.0,
        max_iob=12.0, max_basal=4.0, current_basal=subject.basal_u_per_min * 60,
        enable_auto_isf=auto_isf, smb_ratio_3min=smb_ratio_3min, algorithm=algorithm,
        tdd=tdi,
    )


def export_run(
    subject_name: str, scenario_name: str, auto_isf: bool, prefs: dict, noise: dict, bridge: Bridge,
) -> dict:
    subject = load_subject(subject_name)
    scenario, pump_config = build_scenario(scenario_name)
    profile = profile_for(subject, auto_isf)
    seed = zlib.crc32(subject_name.encode()) & 0xFFFF
    init_bridge(bridge, subject, prefs, INSULIN, profile, scenario)
    loop = run_closed_loop(
        subject, bridge, profile, scenario, seed=seed, residuals=RESIDUALS,
        pump_config=pump_config, **noise,
    )
    metrics = compute_metrics(np.array(loop.cgm), loop.dose_events).as_dict()
    smb = [[round(t, 1), round(u, 2)] for t, u, kind in loop.dose_events if kind == "bolus"]
    return {
        "id": f"{subject_name}-{scenario_name}-{'on' if auto_isf else 'off'}",
        "subject": subject_name,
        "scenario": scenario_name,
        "arm": "on" if auto_isf else "off",
        "cadence_min": 5,
        "duration_min": scenario.duration_min,
        "meals": [[round(t, 1), g] for t, g in scenario.meals],
        "metrics": {key: round(value, 4) for key, value in metrics.items()},
        "time": [round(t, 1) for t in loop.time],
        "cgm": [round(v, 1) for v in loop.cgm],
        "plasma": [round(v, 1) for v in loop.glucose],
        "iob": [round(v, 3) for v in loop.iob],
        "cob": [round(v, 1) for v in loop.cob],
        "rate": [round(v, 3) for v in loop.rate],
        "sens_ratio": [round(v / profile.sens, 3) for v in loop.variable_sens],
        "smb": smb,
        "reason": [text[-220:] for text in loop.reason],
    }


RUNS = [
    ("adult#001", "fcl-24h", False),
    ("adult#001", "fcl-24h", True),
    ("adolescent#001", "fcl-24h", False),
    ("adolescent#001", "fcl-24h", True),
    ("child#005", "fcl-24h", False),
    ("child#005", "fcl-24h", True),
    ("adolescent#001", "fcl-exercise", False),
    ("adolescent#001", "fcl-exercise", True),
    ("adolescent#001", "announced-8h", False),
    ("adolescent#001", "fault-delay", False),
    ("adolescent#001", "cgm-bias", False),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    add_options(ap)
    args = ap.parse_args()
    prefs = apply_prefs(PREFS, args.pref)
    noise = noise_kwargs(args)
    started = time.time()
    runs = []
    with Bridge() as bridge:
        for subject, name, auto_isf in RUNS:
            run = export_run(subject, name, auto_isf, prefs, noise, bridge)
            runs.append(run)
            print(f"{run['id']:40s} TIR={run['metrics']['tir_70_180']:.3f} "
                  f"TBR={run['metrics']['tbr_70']:.3f} dose={run['metrics']['total_insulin']:.1f}", flush=True)

    out = ROOT / "web" / "data" / "runs.js"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": "UVA/Padova-like (S2017 core, derived cohort)",
        "runs": runs,
    }
    out.write_text("window.SIM_RUNS = " + json.dumps(payload, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"written {out} ({len(runs)} runs, {round(time.time() - started, 1)} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
