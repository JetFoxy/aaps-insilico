"""CGM measurement chain: lag, calibration, noise, cadence, rounding, saturation.

The lag stage is a first-order interstitial filter. The core already has a subcutaneous glucose
state (`Gsub`), so the default `tau_lag_min=0` means "the trace is already interstitial"; set it
only when the input is plasma glucose.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .noise import BlockNoise


@dataclass
class SensorConfig:
    cadence_min: float = 5.0
    tau_lag_min: float = 0.0
    calibration_scale: float = 1.0
    calibration_offset: float = 0.0
    noise: BlockNoise | None = None
    quantization: float = 1.0
    low: float = 39.0
    high: float = 400.0


def _lag(t_min: np.ndarray, glucose: np.ndarray, tau_min: float) -> np.ndarray:
    if tau_min <= 0:
        return glucose.copy()
    dt = float(np.median(np.diff(t_min)))
    alpha = dt / (tau_min + dt)
    out = np.empty_like(glucose)
    out[0] = glucose[0]
    for index in range(1, glucose.size):
        out[index] = out[index - 1] + alpha * (glucose[index] - out[index - 1])
    return out


def measure(t_min: np.ndarray, glucose: np.ndarray, config: SensorConfig) -> tuple[np.ndarray, np.ndarray]:
    """Sample a fine glucose trace at the requested cadence and apply the sensor chain."""
    t_min = np.asarray(t_min, dtype=float)
    glucose = np.asarray(glucose, dtype=float)
    lagged = _lag(t_min, glucose, config.tau_lag_min)

    grid = np.arange(t_min[0], t_min[-1] + 0.5 * config.cadence_min, config.cadence_min)
    indices = np.searchsorted(t_min, grid, side="right") - 1
    indices = np.clip(indices, 0, lagged.size - 1)
    sampled = lagged[indices]

    calibrated = config.calibration_scale * sampled + config.calibration_offset
    if config.noise is not None:
        calibrated = calibrated + config.noise.sample(calibrated.size)

    step = config.quantization
    quantized = np.round(calibrated / step) * step
    clamped = np.clip(quantized, config.low, config.high)
    return grid, clamped
