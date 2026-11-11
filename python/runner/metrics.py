"""Metrics over a closed-loop trace: TIR, TBR, LBGI, insulin while falling, SMB count, dose."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def lbgi(values: np.ndarray) -> float:
    """Kovatchev low blood glucose index: mean of 10*f(BG)^2 for f(BG) < 0."""
    bg = np.asarray(values, dtype=float)
    f = 1.509 * (np.log(bg) ** 1.084 - 5.381)
    low = np.where(f < 0, 10.0 * f ** 2, 0.0)
    return float(low.mean())


@dataclass
class Metrics:
    n: int
    mean_glucose: float
    tir_70_180: float
    tbr_70: float
    tbr_54: float
    lbgi: float
    smb_count: int
    total_insulin: float
    insulin_while_falling: float
    insulin_while_falling_fraction: float

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "mean_glucose": self.mean_glucose,
            "tir_70_180": self.tir_70_180,
            "tbr_70": self.tbr_70,
            "tbr_54": self.tbr_54,
            "lbgi": self.lbgi,
            "smb_count": self.smb_count,
            "total_insulin": self.total_insulin,
            "insulin_while_falling": self.insulin_while_falling,
            "insulin_while_falling_fraction": self.insulin_while_falling_fraction,
        }


def compute_metrics(
    glucose: np.ndarray,
    dose_events: list[tuple[float, float, str]],
    cadence_min: float = 5.0,
) -> Metrics:
    glucose = np.asarray(glucose, dtype=float)
    in_range = (glucose >= 70.0) & (glucose <= 180.0)
    total = sum(units for _, units, _ in dose_events)
    falling = 0.0
    for index in range(1, glucose.size):
        if glucose[index] - glucose[index - 1] < 0:
            time_min = index * cadence_min
            falling += sum(units for t, units, _ in dose_events if abs(t - time_min) < cadence_min)
    smb_count = sum(1 for _, _, kind in dose_events if kind == "bolus")
    return Metrics(
        n=int(glucose.size),
        mean_glucose=float(glucose.mean()),
        tir_70_180=float(in_range.mean()),
        tbr_70=float((glucose < 70.0).mean()),
        tbr_54=float((glucose < 54.0).mean()),
        lbgi=lbgi(glucose),
        smb_count=smb_count,
        total_insulin=float(total),
        insulin_while_falling=float(falling),
        insulin_while_falling_fraction=float(falling / total) if total > 0 else 0.0,
    )
