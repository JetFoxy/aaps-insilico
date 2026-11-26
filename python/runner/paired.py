"""Paired per-subject signature deltas: bootstrap CI, win count and an exact sign test."""
from __future__ import annotations

import math

import numpy as np


def paired_summary(a_rows, b_rows, key: str, n_boot: int = 2000, seed: int = 0) -> dict:
    """Delta of ``b - a`` for one signature, paired by position.

    Missing or non-finite pairs are skipped. The p-value is the two-sided exact binomial
    probability of the observed split among non-zero deltas, which needs no distributional
    assumption and is honest at n = 30.
    """
    pairs = []
    for a_row, b_row in zip(a_rows, b_rows):
        a = float(a_row.get(key, float("nan")))
        b = float(b_row.get(key, float("nan")))
        if np.isfinite(a) and np.isfinite(b):
            pairs.append((a, b))
    if not pairs:
        return {
            "key": key, "n": 0, "median_delta": float("nan"),
            "ci95": (float("nan"), float("nan")),
            "wins": 0, "losses": 0, "p": float("nan"),
        }
    deltas = np.array([b - a for a, b in pairs], dtype=float)
    rng = np.random.default_rng(seed)
    index = rng.integers(0, deltas.size, size=(n_boot, deltas.size))
    medians = np.median(deltas[index], axis=1)
    wins = int((deltas > 0).sum())
    losses = int((deltas < 0).sum())
    non_tie = wins + losses
    if non_tie == 0:
        p = 1.0
    else:
        tail = sum(math.comb(non_tie, k) for k in range(min(wins, losses) + 1))
        p = min(1.0, 2.0 * tail / (2 ** non_tie))
    return {
        "key": key,
        "n": len(pairs),
        "median_delta": float(np.median(deltas)),
        "ci95": (float(np.percentile(medians, 2.5)), float(np.percentile(medians, 97.5))),
        "wins": wins,
        "losses": losses,
        "p": float(p),
    }
