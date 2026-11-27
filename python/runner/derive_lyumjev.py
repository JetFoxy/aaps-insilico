"""Derive Lyumjev SC parameters from the published mean concentration curve.

Not a measurement: a fit of the same three-compartment structure to the label's mean curve at
15 U, with ke fixed at the lispro population value and VI free inside the published range.
The result is labelled derived and is opt-in only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from simcore.insulin_pk import LYUMJEV_LABEL, plasma_curve

KE = 0.113
BW = 70.0
BOUNDS = (
    (0.1, 30.0),      # tau, min
    (1e-6, 0.02),     # ka1
    (0.001, 0.06),    # ka2
    (0.001, 0.2),     # alpha
    (0.05, 0.35),     # vi, L/kg
)
STARTS = (
    (6.0, 0.001, 0.013, 0.015, 0.15),
    (3.0, 0.005, 0.02, 0.03, 0.120),
    (12.0, 0.0005, 0.008, 0.01, 0.20),
)


def _unpack(vector):
    return {
        "tau": float(vector[0]), "ka1": float(vector[1]), "ka2": float(vector[2]),
        "alpha": float(vector[3]), "vi": float(vector[4]),
    }


def fit_lyumjev(times, concentration, dose_u=15.0, ke=KE, bw=BW):
    times = np.asarray(times, dtype=float)
    concentration = np.asarray(concentration, dtype=float)
    valid = concentration > 0
    times, concentration = times[valid], concentration[valid]

    def residual(vector):
        values = plasma_curve(dose_u, times, ke=ke, bw=bw, **_unpack(vector))
        return (values - concentration) / concentration

    best = None
    for start in STARTS:
        result = least_squares(
            residual, np.array(start, dtype=float),
            bounds=([low for low, _ in BOUNDS], [high for _, high in BOUNDS]),
            x_scale="jac", max_nfev=4000,
        )
        if best is None or result.cost < best.cost:
            best = result
    params = _unpack(best.x)
    params.update({"ke": ke, "bw": bw, "dose_u": dose_u})
    fine = np.linspace(0.0, 360.0, 721)
    curve = plasma_curve(
        dose_u, fine,
        tau=params["tau"], ka1=params["ka1"], ka2=params["ka2"],
        alpha=params["alpha"], vi=params["vi"], ke=ke, bw=bw,
    )
    peak = int(np.argmax(curve))
    above_half = np.where(curve >= 0.5 * curve[peak])[0]
    params.update({
        "ok": bool(np.max(np.abs(residual(best.x))) <= 0.15),
        "tmax_min": float(fine[peak]),
        "half_max_min": float(fine[above_half[0]]),
        "max_relative_error": float(np.max(np.abs(residual(best.x)))),
        "cost": float(best.cost),
    })
    return params


def main() -> int:
    label = LYUMJEV_LABEL
    fit = fit_lyumjev(
        label["times_min"], label["concentration_pmol_l"],
        dose_u=label["dose_u"], ke=KE, bw=BW,
    )
    fit["label"] = label
    out = ROOT / "out/lyumjev_fit.json"
    out.write_text(json.dumps(fit, indent=2), encoding="utf-8")
    for key, value in fit.items():
        if key not in ("label",):
            print(f"{key:20s} {value}")
    print(f"written {out}")
    return 0 if fit["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
