"""Shared CLI options: cohort size, CGM noise model, preference overrides."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np

from simcore.cohort import extend_cohort
from simcore.params import Subject, load_subjects

ROOT = Path(__file__).resolve().parents[2]
RESIDUALS = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")


def env(name: str, default=None):
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def env_flag(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_prefs() -> list[str]:
    """Preference overrides from SIM_PREFS_FILE (JSON object) and SIM_PREFS (key=value;key=value)."""
    items: list[str] = []
    file_path = env("SIM_PREFS_FILE")
    if file_path:
        data = json.loads(Path(file_path).read_text())
        for key, value in data.items():
            if isinstance(value, bool):
                value = "true" if value else "false"
            items.append(f"{key}={value}")
    raw = env("SIM_PREFS")
    if raw:
        items.extend(part.strip() for part in raw.split(";") if part.strip())
    return items


def add_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cohort-copies", type=int, default=int(env("SIM_COHORT_COPIES", 0)),
                        help="extra absorption-perturbed copies per base subject (30 -> 30*(1+N))")
    parser.add_argument("--cohort-seed", type=int, default=int(env("SIM_COHORT_SEED", 20261111)))
    parser.add_argument("--noise", default=env("SIM_NOISE", "residuals"),
                        choices=("residuals", "ar1", "white", "none"))
    parser.add_argument("--noise-scale", type=float, default=float(env("SIM_NOISE_SCALE", 1.0)),
                        help="multiplier on the noise amplitude")
    parser.add_argument("--noise-sigma", type=float, default=float(env("SIM_NOISE_SIGMA", 2.49)),
                        help="sigma for ar1/white noise, mg/dL")
    parser.add_argument("--noise-ar1", type=float, default=float(env("SIM_NOISE_AR1", 0.8)),
                        help="lag-1 correlation for ar1 noise")
    parser.add_argument("--pref", action="append", default=env_prefs(), metavar="KEY=VALUE",
                        help="override an AAPS preference for every run (repeatable; SIM_PREFS, SIM_PREFS_FILE)")


def load_cohort(copies: int, seed: int) -> dict[str, Subject]:
    base = load_subjects()
    return extend_cohort(base, copies, seed) if copies > 0 else base


def noise_kwargs(args: argparse.Namespace) -> dict:
    return {
        "noise_kind": args.noise,
        "noise_scale": args.noise_scale,
        "noise_sigma": args.noise_sigma,
        "noise_rho": args.noise_ar1,
    }


def _coerce(value: str):
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


def apply_prefs(prefs: dict, items: list[str]) -> dict:
    out = dict(prefs)
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--pref expects KEY=VALUE, got {item!r}")
        out[key] = _coerce(value)
    return out
