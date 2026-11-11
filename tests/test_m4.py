"""M4 acceptance tests: real-residual noise spectrum, lag, cadence, rounding, determinism."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from cgm.noise import AR1Noise, BlockNoise
from cgm.residuals import psd_slope
from cgm.sensor import SensorConfig, measure

FIXTURE = Path(__file__).with_name("fixtures") / "cgm_residuals.npy"


def test_block_noise_reproduces_real_spectrum() -> None:
    residuals = np.load(FIXTURE)
    synthetic = BlockNoise(residuals, seed=11).sample(3000)
    real_slope = psd_slope(residuals, 1 / 3)
    synthetic_slope = psd_slope(synthetic, 1 / 3)
    print(f"PSD slope real {real_slope:.2f}, synthetic {synthetic_slope:.2f}")
    assert abs(real_slope - synthetic_slope) < 0.6
    assert abs(synthetic.std() - residuals.std()) / residuals.std() < 0.25


def test_noise_is_deterministic_by_seed() -> None:
    residuals = np.load(FIXTURE)
    first = BlockNoise(residuals, seed=5).sample(500)
    second = BlockNoise(residuals, seed=5).sample(500)
    other = BlockNoise(residuals, seed=6).sample(500)
    assert np.array_equal(first, second)
    assert not np.array_equal(first, other)


def test_ar1_fallback_has_requested_rho_and_sigma() -> None:
    signal = AR1Noise(rho=0.75, sigma=2.0, seed=3).sample(20000)
    lag1 = np.corrcoef(signal[:-1], signal[1:])[0, 1]
    assert abs(lag1 - 0.75) < 0.05
    assert abs(signal.std() - 2.0) < 0.2


def test_lag_delays_a_step_by_first_order_constant() -> None:
    t = np.arange(0.0, 300.0, 1.0)
    glucose = np.where(t < 100.0, 100.0, 200.0)
    grid, values = measure(t, glucose, SensorConfig(cadence_min=1.0, tau_lag_min=10.0, noise=None))
    crossing = grid[values >= 150.0][0]
    assert abs(crossing - (100.0 + 0.693 * 10.0)) < 2.0


def test_cadence_rounding_and_saturation() -> None:
    t = np.arange(0.0, 601.0, 1.0)
    glucose = 100.0 + 0.05 * t
    grid, values = measure(t, glucose, SensorConfig(cadence_min=5.0, noise=None, quantization=1.0))
    assert np.allclose(np.diff(grid), 5.0)
    assert grid.size == 121
    assert np.all(values == np.round(values))
    assert values.min() >= 39.0 and values.max() <= 400.0
