"""Second model for M8: Deichmann et al. 2023 (vendored BSD-3 code, not reimplemented)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "deichmann"


def run_deichmann(
    ac_counts: np.ndarray,
    meals_mg: dict[int, float],
    weight_kg: float = 70.0,
    gb: float = 120.0,
    ib: float = 12.0,
    params_file: str = "params_standard.csv",
) -> np.ndarray:
    """Glucose [mg/dl] for an AC-count profile and meal inputs (minute -> mg CHO)."""
    if not VENDOR.exists():
        raise FileNotFoundError(f"vendored Deichmann model not found at {VENDOR}")
    import pandas as pd

    old_cwd = os.getcwd()
    sys.path.insert(0, str(VENDOR))
    os.chdir(VENDOR)
    try:
        import functions.PAmodel as GIM

        params = pd.read_csv(
            f"parameters/{params_file}", header=None, dtype={0: str}, delimiter=";"
        ).set_index(0).squeeze().to_dict()
        t_sim = len(ac_counts)
        meal = np.zeros(t_sim)
        for minute, mg in meals_mg.items():
            meal[minute] = mg
        u = np.zeros(t_sim)
        model = GIM.compute_glucose_simulation(
            params, [gb, ib], weight_kg, np.asarray(ac_counts, dtype=float), meal, u=u, bolus_calc=True
        )
        return model["G"].to_numpy()
    finally:
        os.chdir(old_cwd)
        if sys.path and sys.path[0] == str(VENDOR):
            sys.path.pop(0)
