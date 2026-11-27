"""Colmegna MM structure: forward curve and constrained KM/VI derivation."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.derive_lyumjev_mm import fit_mm, mm_curve


def test_mm_curve_peaks_and_decays():
    times = np.linspace(0, 360, 361)
    c = mm_curve(times, km_lis=1000.0, vi=0.10)
    assert c[0] == 0.0
    peak = int(np.argmax(c))
    assert 0 < peak < len(c) - 1
    assert c[-1] < c[peak]


def test_fit_on_five_points_is_underdetermined():
    times = np.array([60, 120, 180, 240, 300], dtype=float)
    target = mm_curve(times, km_lis=1500.0, vi=0.11)
    fit = fit_mm(times, target)
    assert set(fit) >= {"km_lis", "vi", "max_relative_error", "ok"}
    assert fit["max_relative_error"] > 0.10
