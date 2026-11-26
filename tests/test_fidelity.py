"""Fidelity signatures: each statistic on a trace whose answer is known."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.fidelity import (
    SIGNATURES, _compression, _diurnal, _hypo, _lag_corr, evaluate, in_real_range,
    insulin_from_events, insulin_weekly_cv, trace, weekly_sens_cv,
)


def test_constant_trace_has_no_variability_or_noise():
    t = np.arange(0, 24 * 3600, 300.0)
    d = trace(t, np.full_like(t, 100.0))
    values = {key: fn(d) for key, fn in SIGNATURES}
    assert values["cv"] == 0.0
    assert values["tail"] == 0.0
    assert values["noise"] == 0.0
    assert in_real_range("cv", values["cv"]) is False


def test_diurnal_amplitude_of_a_day_curve():
    t = np.arange(0, 24 * 3600, 300.0)
    bg = 120.0 + 20.0 * np.sin(2 * np.pi * t / 86400.0)
    assert _diurnal(trace(t, bg)) > 20.0


def test_lag_correlation_of_a_slow_curve():
    t = np.arange(0, 6 * 3600, 300.0)
    bg = 120.0 + 10.0 * np.sin(2 * np.pi * t / 28800.0)
    assert _lag_corr(t, bg, 1800.0) > 0.5


def test_hypo_recovery_and_compression_detector():
    t = np.arange(0, 6 * 3600, 300.0)
    bg = np.full_like(t, 110.0)
    start = 60
    bg[start:start + 6] = [65.0, 60.0, 55.0, 60.0, 80.0, 110.0]
    d = trace(t, bg)
    recovery, rebound = _hypo(d)
    assert recovery > 0 and np.isfinite(recovery)
    assert rebound == 0.0
    dip = np.full_like(t, 100.0)
    dip[start:start + 6] = [90.0, 70.0, 60.0, 70.0, 90.0, 100.0]
    assert _compression(trace(t, dip)) >= 30.0


def test_evaluate_returns_every_signature():
    t = np.arange(0, 24 * 3600, 300.0)
    stats = evaluate([trace(t, 120.0 + 10.0 * np.sin(t / 3600.0))])
    assert set(stats) == {key for key, _ in SIGNATURES}
    assert np.isfinite(stats["cv"][0])


def test_insulin_weekly_cv_of_a_constant_series_is_zero():
    assert insulin_weekly_cv(np.full(21 * 288, 2.0), 5.0) == 0.0


def test_insulin_weekly_cv_detects_one_doubled_week():
    series = np.full(21 * 288, 1.0)
    series[7 * 288:14 * 288] = 2.0
    value = insulin_weekly_cv(series, 5.0)
    assert 30.0 < value < 45.0


def test_insulin_weekly_cv_is_undefined_before_three_weeks():
    assert np.isnan(insulin_weekly_cv(np.full(14 * 288, 1.0), 5.0))


def test_weekly_sens_cv_reads_the_applied_schedule():
    sens = np.empty(21 * 288)
    sens[:7 * 288] = 50.0
    sens[7 * 288:14 * 288] = 60.0
    sens[14 * 288:] = 40.0
    assert abs(weekly_sens_cv(sens, 5.0) - 16.33) < 0.5


def test_insulin_from_events_sums_basal_and_bolus_per_cycle():
    events = [(5.0, 0.1, "basal"), (5.0, 1.0, "bolus"), (10.0, 0.1, "basal")]
    series = insulin_from_events(events, n_cycles=3, cadence_min=5.0)
    np.testing.assert_allclose(series, [1.1, 0.1, 0.0])


def test_evaluate_reports_drift_and_insulin_cv_only_when_the_trace_carries_them():
    t = np.arange(0, 21 * 24 * 3600, 300.0)
    plain = trace(t, np.full_like(t, 120.0))
    stats = evaluate([plain])
    assert np.isnan(stats["drift"][0])
    assert np.isnan(stats["insulin_cv"][0])
    carried = dict(plain, insulin=np.full(t.size, 1.0), sens=np.full(t.size, 50.0))
    stats = evaluate([carried])
    assert stats["insulin_cv"][0] == 0.0
    assert stats["drift"][0] == 0.0
