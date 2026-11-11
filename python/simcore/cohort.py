"""Extended derived cohort: absorption-perturbed copies of the base vectors.

Only parameters that do not enter the fasting steady state are perturbed (meal absorption
`kabs/kmax/kmin`, insulin absorption `ka1/ka2/kd`, subcutaneous glucose `ksc`). The subcutaneous
insulin steady state is recomputed analytically from the model equations, so every copy starts at
the same fasting glucose as its base subject. `Vmx`, `kp3`, `BW`, `Gb` and `u2ss` are *not*
perturbed: the population vector for them is proprietary and re-equilibration is not validated.

Perturbation is log-normal with sigma 0.15, deterministic by seed. The result is a *derived*
extension, not the S2017 population.
"""
from __future__ import annotations

import numpy as np

from .params import Subject, load_subjects

PERTURBED = ("kabs", "kmax", "kmin", "ka1", "ka2", "kd", "ksc")
SIGMA = 0.15


def perturb_subject(base: Subject, rng: np.random.Generator, name: str, sigma: float = SIGMA) -> Subject:
    params = dict(base.params)
    for key in PERTURBED:
        params[key] = base.params[key] * float(np.exp(rng.normal(0.0, sigma)))

    insulin_ss = base.params["isc1ss"] * (base.params["ka1"] + base.params["kd"])
    isc1 = insulin_ss / (params["ka1"] + params["kd"])
    isc2 = params["kd"] * isc1 / params["ka2"]
    params["isc1ss"] = isc1
    params["isc2ss"] = isc2

    state = base.state0.copy()
    state[10] = isc1
    state[11] = isc2
    return Subject(name=name, state0=state, params=params)


def extend_cohort(
    subjects: dict[str, Subject] | None = None,
    copies: int = 3,
    seed: int = 20261111,
    sigma: float = SIGMA,
) -> dict[str, Subject]:
    """Return the base subjects plus `copies` perturbed copies of each (names `adult#001-v01`)."""
    subjects = subjects or load_subjects()
    if copies <= 0:
        return dict(subjects)
    rng = np.random.default_rng(seed)
    out = dict(subjects)
    for name in sorted(subjects):
        for index in range(1, copies + 1):
            copy_name = f"{name}-v{index:02d}"
            out[copy_name] = perturb_subject(subjects[name], rng, copy_name, sigma)
    return out
