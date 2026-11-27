"""Constrained Lyumjev derivation in the Colmegna 2024 Michaelis-Menten structure.

Structure and URLi/lispro ratios: Colmegna et al., J Diabetes Sci Technol 2024;18(3):676-685,
PMC11089876 (ka1 x13, kd x1.45, tau x0.5, KM x2.1). Base lispro parameters: Schiavon 2018 /
Faggionato 2021. Bioavailability 0.65: Linnebjerg et al., Clin Pharmacokinet 2020;59:1589.

Only KM_LIS and VI are fitted to the published label curve; everything else is fixed. The result
is labelled derived, not published, and is opt-in only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from simcore.insulin_pk import LYUMJEV_LABEL

U_TO_PMOL = 6000.0
BIOAVAILABILITY = 0.65
KA1_LIS = 0.000134
KA2_LIS = 0.0128
KD_LIS = 0.0283
TAU_LIS = 5.62
KE = 0.113
BW = 70.0
RATIO_KA1 = 13.0
RATIO_KD = 1.45
RATIO_TAU = 0.5
RATIO_KM = 2.1


def mm_curve(times, km_lis: float, vi: float, dose_u: float = 15.0) -> np.ndarray:
    """Plasma concentration in pmol/L for the MM structure at the URLi ratios."""
    times = np.asarray(times, dtype=float)
    ka1 = RATIO_KA1 * KA1_LIS
    ka2 = KA2_LIS
    kd = RATIO_KD * KD_LIS
    tau = RATIO_TAU * TAU_LIS
    km = RATIO_KM * km_lis
    dose = dose_u * BIOAVAILABILITY * U_TO_PMOL

    def rhs(_t, y):
        isc1, isc2, ip = y
        flux = kd * km / (km + isc1) * isc1
        return [
            -ka1 * isc1 - flux,
            -ka2 * isc2 + flux,
            -KE * ip + ka1 * isc1 + ka2 * isc2,
        ]

    end = max(float(times.max()) + 1.0, tau + 1.0)
    dense = np.linspace(tau, end, 1200)
    solution = solve_ivp(rhs, (tau, end), [dose, 0.0, 0.0], t_eval=dense, rtol=1e-8, atol=1e-8)
    concentration = solution.y[2] / (vi * BW)
    return np.interp(times, solution.t, concentration, left=0.0, right=0.0)


def _metrics(km_lis: float, vi: float, dose_u: float = 15.0) -> tuple[float, float]:
    fine = np.linspace(0.0, 360.0, 721)
    curve = mm_curve(fine, km_lis, vi, dose_u)
    peak = int(np.argmax(curve))
    above_half = np.where(curve >= 0.5 * curve[peak])[0]
    return float(fine[peak]), float(fine[above_half[0]])


def fit_mm(times, concentration, dose_u: float = 15.0) -> dict:
    times = np.asarray(times, dtype=float)
    concentration = np.asarray(concentration, dtype=float)
    valid = concentration > 0
    times, concentration = times[valid], concentration[valid]

    def residual(log_params):
        km = 10.0 ** log_params[0]
        vi = 10.0 ** log_params[1]
        model = mm_curve(times, km, vi, dose_u)
        rel = (model - concentration) / concentration
        tmax, half = _metrics(km, vi, dose_u)
        return np.concatenate([rel, [(tmax - 57.0) / 10.0, (half - 13.0) / 6.0]])

    best = None
    for km0 in (100.0, 1000.0, 10000.0):
        for vi0 in (0.08, 0.12, 0.20):
            result = least_squares(
                residual, np.log10([km0, vi0]),
                bounds=([0.0, np.log10(0.05)], [7.0, np.log10(0.30)]),
                max_nfev=3000,
            )
            if best is None or result.cost < best.cost:
                best = result
    km = float(10.0 ** best.x[0])
    vi = float(10.0 ** best.x[1])
    model = mm_curve(times, km, vi, dose_u)
    error = float(np.max(np.abs((model - concentration) / concentration)))
    tmax, half = _metrics(km, vi, dose_u)
    return {
        "km_lis": km,
        "vi": vi,
        "km_urli": RATIO_KM * km,
        "max_relative_error": error,
        "tmax_min": tmax,
        "half_max_min": half,
        "ok": bool(error <= 0.10 and 50.0 <= tmax <= 65.0 and 8.0 <= half <= 20.0),
        "cost": float(best.cost),
    }


def main() -> int:
    fit = fit_mm(
        LYUMJEV_LABEL["times_min"], LYUMJEV_LABEL["concentration_pmol_l"],
    )
    fit["source"] = (
        "structure/ratios Colmegna 2024; base lispro Schiavon 2018/Faggionato 2021; "
        "bioavailability 0.65 Linnebjerg 2020; KM_LIS/VI derived from the label curve"
    )
    out = ROOT / "out/lyumjev_mm_fit.json"
    out.write_text(json.dumps(fit, indent=2), encoding="utf-8")
    for key, value in fit.items():
        print(f"{key:22s} {value}")
    print(f"written {out}")
    return 0 if fit["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
