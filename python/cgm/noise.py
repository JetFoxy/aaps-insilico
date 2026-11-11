"""CGM noise: block replay of real residuals, AR(1) only as a documented fallback."""
from __future__ import annotations

import numpy as np


class BlockNoise:
    """Replays contiguous real residual windows, so the spectrum is the measured one.

    A window is the length of the requested run when the residual series is long enough; short
    blocks would flatten the low-frequency slope (30-sample blocks: PSD slope 1.7 vs 4.7 real).
    Longer runs are tiled from full-length windows.
    """

    def __init__(self, residuals: np.ndarray, block: int = 0, seed: int = 0, scale: float = 1.0):
        if residuals.size < 16:
            raise ValueError("residual series too short")
        self.residuals = np.asarray(residuals, dtype=float)
        self.block = int(block)
        self.scale = float(scale)
        self.rng = np.random.default_rng(seed)

    def sample(self, n: int) -> np.ndarray:
        window = self.block if self.block > 0 else n
        window = min(window, self.residuals.size)
        pieces = []
        produced = 0
        while produced < n:
            start = int(self.rng.integers(0, self.residuals.size - window + 1))
            piece = self.residuals[start:start + window]
            pieces.append(piece)
            produced += piece.size
        return np.concatenate(pieces)[:n] * self.scale


class AR1Noise:
    """Fallback only: x[k] = rho*x[k-1] + eps, sigma from the real residual scale."""

    def __init__(self, rho: float, sigma: float, seed: int = 0):
        self.rho = float(rho)
        self.sigma = float(sigma)
        self.rng = np.random.default_rng(seed)

    def sample(self, n: int) -> np.ndarray:
        eps = self.rng.normal(0.0, self.sigma * np.sqrt(1.0 - self.rho ** 2), n)
        out = np.empty(n)
        previous = 0.0
        for index, value in enumerate(eps):
            previous = self.rho * previous + value
            out[index] = previous
        return out


class WhiteNoise:
    """Independent Gaussian noise with a fixed sigma, for controlled sensitivity sweeps."""

    def __init__(self, sigma: float, seed: int = 0):
        self.sigma = float(sigma)
        self.rng = np.random.default_rng(seed)

    def sample(self, n: int) -> np.ndarray:
        return self.rng.normal(0.0, self.sigma, n)
