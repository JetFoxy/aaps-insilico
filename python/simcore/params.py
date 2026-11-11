"""Subject parameters for the S2017-like core.

Source of the vendored CSV: simglucose 0.2.11 (`simglucose/params/vpatient_params.csv`),
which encodes the S2008/S2013 parameter vectors. These are **derived** parameters, not the
official S2017 population: see docs/registry/parameters.md §3.3 and docs/acceptance/M0.md.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

PARAM_FILE = Path(__file__).with_name("params") / "vpatient_params.csv"

STATE_NAMES = (
    "Qsto1", "Qsto2", "Qgut", "Gp", "Gt", "Ip", "X", "I1", "XL", "Il", "Isc1", "Isc2", "Gsub",
)

PARAM_NAMES = (
    "BW", "EGPb", "Gb", "Ib", "kabs", "kmax", "kmin", "b", "d", "Vg", "Vi", "Ipb", "Vmx",
    "Km0", "k2", "k1", "p2u", "m1", "m5", "CL", "HEb", "m2", "m4", "m30", "Ilb", "ki",
    "kp2", "kp3", "f", "Gpb", "ke1", "ke2", "Fsnc", "Gtb", "Vm0", "Rdb", "PCRb", "kd",
    "ksc", "ka1", "ka2", "dosekempt", "u2ss", "isc1ss", "isc2ss", "kp1", "patient_history",
)


@dataclass(frozen=True)
class Subject:
    name: str
    state0: np.ndarray
    params: dict[str, float]

    def __getattr__(self, item: str) -> float:
        try:
            params = object.__getattribute__(self, "params")
        except AttributeError as exc:
            raise AttributeError(item) from exc
        try:
            return params[item]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(item) from exc

    @property
    def basal_u_per_min(self) -> float:
        """Basal insulin infusion that holds the subject at Gb (U/min)."""
        return self.params["u2ss"] * self.params["BW"] / 6000.0

    @property
    def gb(self) -> float:
        return self.params["Gb"]

    @property
    def weight_kg(self) -> float:
        return self.params["BW"]


def load_subjects(path: Path | str = PARAM_FILE) -> dict[str, Subject]:
    path = Path(path)
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    subjects: dict[str, Subject] = {}
    for row in rows:
        state = np.array([float(row.get(f"x0_ {i}") or row[f"x0_{i}"]) for i in range(1, 14)], dtype=float)
        params = {name: float(row[name]) for name in PARAM_NAMES if name != "patient_history"}
        subjects[row["Name"]] = Subject(name=row["Name"], state0=state, params=params)
    return subjects


def load_subject(name: str, path: Path | str = PARAM_FILE) -> Subject:
    subjects = load_subjects(path)
    if name not in subjects:
        raise KeyError(f"unknown subject {name!r}; available: {', '.join(sorted(subjects))}")
    return subjects[name]
