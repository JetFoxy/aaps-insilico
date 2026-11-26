"""Pre-registered gate tests: criteria, fingerprint, exposure and the low-duration metric."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.gate import DEFAULT_CRITERIA, Criterion, evaluate, fingerprint, report
from runner.metrics import compute_metrics


def metrics(**overrides):
    base = {
        "minutes_below_54_per_day": 0.0,
        "longest_below_54_min": 0.0,
        "tir_70_180": 0.75,
        "tbr_70": 0.04,
    }
    base.update(overrides)
    return base


def test_criterion_measure_and_direction():
    criterion = Criterion("tir_70_180", "median", 0.70, "at_least", "control", "consensus")
    assert criterion.measure([metrics(tir_70_180=0.8), metrics(tir_70_180=0.6)]) == 0.7
    assert criterion.holds(0.7) is True
    assert criterion.holds(0.69) is False


def test_fingerprint_is_stable_and_design_sensitive():
    a = fingerprint(DEFAULT_CRITERIA, {"arm": "dynisf", "n": 30})
    b = fingerprint(DEFAULT_CRITERIA, {"n": 30, "arm": "dynisf"})
    c = fingerprint(DEFAULT_CRITERIA, {"arm": "off", "n": 30})
    assert a == b
    assert a != c


def test_gate_passes_clean_records_and_fails_a_low():
    clean = [metrics() for _ in range(30)]
    assert evaluate(clean, days=1.0)["passed"] is True
    assert evaluate([metrics(minutes_below_54_per_day=90.0)] * 30, days=1.0)["passed"] is False


def test_exposure_reports_the_rule_of_three():
    verdict = evaluate([metrics() for _ in range(30)], days=1.0)
    exposure = verdict["exposure"]
    assert exposure["subject_days"] == 30
    assert exposure["severe_events"] == 0
    assert exposure["upper_bound_per_subject_year"] > 30
    assert verdict["fingerprint"] in report(verdict)


def test_longest_low_and_minutes_per_day():
    glucose = np.array([100.0] * 10 + [50.0] * 6 + [100.0] * 10)
    result = compute_metrics(glucose, [], cadence_min=5.0)
    assert result.longest_below_54_min == 30.0
    assert result.minutes_below_54_per_day == 6 / 26 * 1440.0
