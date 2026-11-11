"""Exercise profile from Schiavon et al. 2013 (J Diabetes Sci Technol 7(6):1461-1469).

The paper derives the instantaneous SI multiplier during a 75-min session from the measured
"almost doubling" of the average SI over the whole experiment: alpha = 3.29. The SI profile is
SI_rest before exercise, 3.29*SI_rest during it, and a linear return to SI_rest over 165 min
(195-360 min in the protocol). The insulin-absorption effect is a separate multiplier on ka,
calibrated once to the published +30% plasma insulin (PBPK, PMC8237803); it is not fitted to
glucose.
"""
from __future__ import annotations

from dataclasses import dataclass

SI_GAIN = 3.29
RECOVERY_MIN = 165.0
KA_MULTIPLIER = 1.388  # calibrated once to the published +30% plasma insulin, not to glucose


@dataclass(frozen=True)
class ExerciseSession:
    start_min: float
    duration_min: float
    intensity: float = 1.0
    si_gain: float = SI_GAIN
    recovery_min: float = RECOVERY_MIN

    def _peak(self) -> float:
        return 1.0 + (self.si_gain - 1.0) * max(0.0, min(1.0, self.intensity))

    def active(self, t: float) -> bool:
        return self.start_min <= t < self.start_min + self.duration_min

    def si_multiplier(self, t: float) -> float:
        peak = self._peak()
        if t < self.start_min:
            return 1.0
        if t < self.start_min + self.duration_min:
            return peak
        end = self.start_min + self.duration_min
        if t < end + self.recovery_min:
            fraction = (t - end) / self.recovery_min
            return peak + (1.0 - peak) * fraction
        return 1.0
