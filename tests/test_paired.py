"""Paired per-subject summaries: delta, CI, wins and a sign test."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.paired import paired_summary


def test_paired_summary_counts_consistent_gains():
    a = [{"cv": value} for value in (40.0, 40.0, 40.0, 40.0)]
    b = [{"cv": value} for value in (35.0, 35.0, 35.0, 35.0)]
    result = paired_summary(a, b, "cv")
    assert result["n"] == 4
    assert result["median_delta"] == -5.0
    assert result["wins"] == 0
    assert result["losses"] == 4
    assert result["p"] < 0.2


def test_paired_summary_skips_missing_and_is_zero_on_ties():
    a = [{"cv": 40.0}, {"cv": float("nan")}, {"cv": 30.0}]
    b = [{"cv": 40.0}, {"cv": 35.0}, {"cv": 30.0}]
    result = paired_summary(a, b, "cv")
    assert result["n"] == 2
    assert result["median_delta"] == 0.0
    assert result["wins"] == 0
    assert result["p"] == 1.0
