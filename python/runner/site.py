"""Infusion-site failure: insulin leaves the pump but not all of it reaches the depot.

A deterministic fault window for the fault catalogue. The pump log and the algorithm's IOB
keep counting every delivered unit; the model receives ``residual`` of the commanded basal and
boluses inside the window. The residual range (0.25-0.70) and the delayed-detection mechanism
come from TimSim (MIT, ``timsim/therapy/site.py``), which calibrated them against DCLP3; this
module keeps the deterministic window only, not the stochastic detection model.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteFailure:
    start_min: float
    duration_min: float
    residual: float = 0.40

    def __post_init__(self) -> None:
        if self.duration_min <= 0:
            raise ValueError("site failure duration must be positive")
        if not 0.0 <= self.residual <= 1.0:
            raise ValueError("site residual must be between 0 and 1")

    def fraction(self, minute: float) -> float:
        """Fraction of commanded insulin that reaches the model at ``minute``."""
        if self.start_min <= minute < self.start_min + self.duration_min:
            return self.residual
        return 1.0
