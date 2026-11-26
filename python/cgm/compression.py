"""Compression lows: false sensor dips that the algorithm sees and the body does not.

The dip shape, baseline/nadir bands, minimum depth and event rate follow TimSim (MIT,
``timsim/sensor/realism.py``), which measured them against 192 real users. The artefact is
applied to the CGM entering the loop; the model's true glucose is untouched.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DIP_MINUTES = np.array([0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0])
DIP_SHAPE = np.array([0.0, 0.55, 1.00, 0.85, 0.55, 0.25, 0.0])


@dataclass(frozen=True)
class CompressionConfig:
    rate_per_30d: float = 30.0
    baseline_band: tuple[float, float] = (85.0, 140.0)
    nadir_band: tuple[float, float] = (42.0, 62.0)
    min_depth: float = 26.0
    defer_min: tuple[float, float] = (4.0, 180.0)


class CompressionArtefacts:
    """Schedules compression dips and returns the offset for the current CGM sample."""

    def __init__(
        self,
        config: CompressionConfig,
        seed: int,
        cycles: int,
        cadence_min: float,
    ):
        self.config = config
        self.rng = np.random.default_rng(seed)
        self.cadence_min = float(cadence_min)
        self.shape = np.interp(
            np.arange(0.0, 30.0 + self.cadence_min, self.cadence_min),
            DIP_MINUTES,
            DIP_SHAPE,
        )
        span_days = cycles * self.cadence_min / 1440.0
        count = int(round(config.rate_per_30d * span_days / 30.0))
        first = len(self.shape) + 2
        last = max(cycles - len(self.shape) - 1, first + 1)
        self.pending: list[int] = sorted(
            int(value) for value in self.rng.integers(first, last, size=count)
        ) if count > 0 else []
        self.active: list[tuple[int, float]] = []
        self.events = 0

    def offset(self, index: int, cgm: float) -> float:
        """Negative offset to add to the CGM at ``index``; zero when no dip is active."""
        while self.pending and self.pending[0] <= index:
            start = self.pending.pop(0)
            if index - start > 1:
                continue
            if not (self.config.baseline_band[0] <= cgm <= self.config.baseline_band[1]):
                delay = int(round(self.rng.uniform(*self.config.defer_min) / self.cadence_min))
                self.pending.append(index + max(1, delay))
                self.pending.sort()
                continue
            depth = float(cgm) - float(self.rng.uniform(*self.config.nadir_band))
            if depth <= self.config.min_depth:
                delay = int(round(self.rng.uniform(*self.config.defer_min) / self.cadence_min))
                self.pending.append(index + max(1, delay))
                self.pending.sort()
                continue
            self.active.append((index, depth))
            self.events += 1

        offset = 0.0
        still: list[tuple[int, float]] = []
        for start, depth in self.active:
            elapsed = index - start
            if 0 <= elapsed < len(self.shape):
                offset -= depth * float(self.shape[elapsed])
                still.append((start, depth))
            elif elapsed < 0:
                still.append((start, depth))
        self.active = still
        return offset
