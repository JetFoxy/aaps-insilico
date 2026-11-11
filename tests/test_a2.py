"""A2 acceptance tests: paired statistics and scenario coverage (no bridge needed)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.a2_matrix import SCENARIOS, paired_stats


def _rows(off: list[float], on: list[float]) -> list[dict]:
    return [{"off": {"x": o}, "on": {"x": n}} for o, n in zip(off, on)]


def test_paired_stats_reports_direction_and_counts() -> None:
    rows = _rows([1.0, 2.0, 3.0, 4.0, 5.0], [2.0, 4.0, 6.0, 8.0, 10.0])
    stats = paired_stats(rows, "x")
    assert stats["diff_mean"] == 3.0
    assert stats["diff_median"] == 3.0
    assert stats["improved"] == 5
    assert stats["worsened"] == 0
    assert stats["ci95_low"] <= stats["diff_mean"] <= stats["ci95_high"]


def test_paired_stats_is_deterministic() -> None:
    rows = _rows([1.0, 2.0, 3.0, 4.0, 5.0], [1.5, 1.0, 4.0, 3.0, 9.0])
    first = paired_stats(rows, "x")
    second = paired_stats(rows, "x")
    assert first == second


def test_paired_stats_handles_zero_difference() -> None:
    rows = _rows([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    stats = paired_stats(rows, "x")
    assert stats["diff_mean"] == 0.0
    assert stats["p_wilcoxon"] == 1.0


def test_scenarios_cover_both_meal_modes_and_a_profile_error() -> None:
    modes = {spec["scenario"].meal_mode for spec in SCENARIOS.values()}
    assert modes == {"fcl", "announced"}
    assert any(spec["profile_scale"] != 1.0 for spec in SCENARIOS.values())
