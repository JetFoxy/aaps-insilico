"""Fast-acting lispro PK variant from the published population (opt-in, not the S2008 baseline).

Structure: S2017 Appendix A16-A17. Values: Schiavon et al. 2018, identified on 116 T1D subjects,
with the open population estimates of Faggionato, Schiavon, Dalla Man 2021 (Metabolites, CC BY),
Tables 2-3. The subject draw is lognormal around the population value with the published omega,
clipped to the published individual range. The BMI covariate for ka2 is not applied because the
derived cohort has no height; the reference-BMI value (factor 1) is used.
"""
from __future__ import annotations

import hashlib

import numpy as np

from .params import Subject

U_TO_PMOL = 6000.0

TAU = (5.62, 0.731, (0.748, 21.9))
KA1 = (0.000134, 1.86, (0.000079, 0.00824))
KA2 = (0.0128, 0.322, (0.00418, 0.0313))
ALPHA = (0.0155, 1.02, (0.00364, 0.116))


def _draw(name: str, key: str, population: tuple, seed: int) -> float:
    value, omega, bounds = population
    digest = hashlib.sha256(f"{name}:lispro:{key}:{seed}".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "big"))
    z = float(rng.standard_normal())
    return float(np.clip(value * np.exp(omega * z), *bounds))


def lispro_params(name: str, seed: int = 0) -> dict:
    tau = _draw(name, "tau", TAU, seed)
    ka1 = _draw(name, "ka1", KA1, seed)
    ka2 = _draw(name, "ka2", KA2, seed)
    alpha = _draw(name, "alpha", ALPHA, seed)
    return {"tau": tau, "ka1": ka1, "ka2": ka2, "alpha": alpha, "kd": ka2 + alpha}


def apply_lispro(subject: Subject, seed: int = 0) -> Subject:
    """Return the subject with lispro SC parameters and the matching steady state."""
    draw = lispro_params(subject.name, seed)
    params = dict(subject.params)
    params.update({key: draw[key] for key in ("tau", "ka1", "ka2", "kd")})
    inflow = subject.basal_u_per_min * U_TO_PMOL / params["BW"]
    isc1 = inflow / (params["ka1"] + params["kd"])
    isc2 = params["kd"] * isc1 / params["ka2"]
    state = subject.state0.copy()
    state[10] = isc1
    state[11] = isc2
    return Subject(name=subject.name, state0=state, params=params)


LYUMJEV_LABEL = {
    "times_min": [0.0, 60.0, 120.0, 180.0, 240.0, 300.0],
    "concentration_pmol_l": [0.0, 555.0, 450.0, 205.0, 73.0, 28.0],
    "dose_u": 15.0,
    "source": "Lyumjev US prescribing information, section 12.2 (mean, healthy subjects)",
    "tmax_min": 57.0,
    "half_max_min": 13.0,
}


def plasma_curve(dose_u, times, tau, ka1, ka2, alpha, vi, ke, bw):
    """Plasma lispro concentration after one SC bolus, same three-compartment structure."""
    from scipy.linalg import expm

    kd = float(ka2) + float(alpha)
    matrix = np.array([
        [-(ka1 + kd), 0.0, 0.0],
        [kd, -ka2, 0.0],
        [ka1, ka2, -ke],
    ])
    dose_pmol = float(dose_u) * U_TO_PMOL
    volume_l = float(vi) * float(bw)
    out = np.zeros(len(times), dtype=float)
    for index, time_min in enumerate(times):
        elapsed = float(time_min) - float(tau)
        if elapsed <= 0:
            continue
        state = expm(matrix * elapsed) @ np.array([dose_pmol, 0.0, 0.0])
        out[index] = state[2] / volume_l
    return out
