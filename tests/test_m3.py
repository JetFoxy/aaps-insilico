"""M3 acceptance tests: SI classes, dawn ramp, breakfast vs lunch peak."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from simcore.model import Bolus, Meal, Schedule, TimeVarying, simulate
from simcore.params import load_subject, load_subjects
from simcore.variability import CLASSES, PROBABILITIES, assign_class, si_day


def test_si_class_probabilities_match_visentin_2015() -> None:
    counts = Counter(assign_class(seed) for seed in range(20000))
    for (name, _), probability in zip(CLASSES, PROBABILITIES):
        observed = counts[name] / 20000
        assert abs(observed - probability) < 0.02, f"{name}: {observed:.3f} vs {probability}"


def test_si_multipliers_follow_the_three_segments() -> None:
    tv = TimeVarying(si_multipliers=(0.5, 1.0, 0.7))
    assert tv.si(4 * 60) == 0.5
    assert tv.si(10 * 60 + 59) == 0.5
    assert tv.si(11 * 60) == 1.0
    assert tv.si(16 * 60 + 59) == 1.0
    assert tv.si(17 * 60) == 0.7
    assert tv.si(3 * 60) == 0.7


def test_dawn_ramp_is_linear_between_3_and_7() -> None:
    tv = TimeVarying(dawn_egp=1.5)
    assert tv.dawn_ramp(3 * 60) == 0.0
    assert tv.dawn_ramp(5 * 60) == 0.5
    assert tv.dawn_ramp(7 * 60) == 1.0


def test_dawn_raises_glucose_without_insulin_change() -> None:
    subject = load_subject("adult#001")
    trace = simulate(subject, Schedule(subject.basal_u_per_min), 12 * 60, 1.0, TimeVarying(dawn_egp=1.5))
    rise = float(trace.glucose[7 * 60] - trace.glucose[3 * 60])
    print(f"dawn rise {rise:.1f} mg/dL")
    assert rise > 20.0


def test_breakfast_peak_above_lunch_peak() -> None:
    adults = [s for name, s in sorted(load_subjects().items()) if name.startswith("adult")]
    peaks_b, peaks_l = [], []
    for index, subject in enumerate(adults[:5]):
        day = si_day(1000 + index)
        schedule = Schedule(
            basal_u_per_min=subject.basal_u_per_min,
            meals=[Meal(7 * 60, 60.0), Meal(13 * 60, 60.0), Meal(19 * 60, 60.0)],
            boluses=[Bolus(7 * 60, 60.0 / 10.5), Bolus(13 * 60, 60.0 / 10.5), Bolus(19 * 60, 60.0 / 10.5)],
        )
        trace = simulate(subject, schedule, 24 * 60, 1.0, TimeVarying(si_multipliers=day.multipliers, dawn_egp=1.5))
        g = trace.glucose
        peaks_b.append(float(g[7 * 60:13 * 60].max() - subject.gb))
        peaks_l.append(float(g[13 * 60:19 * 60].max() - subject.gb))
    print(f"breakfast {np.mean(peaks_b):.1f}, lunch {np.mean(peaks_l):.1f}")
    assert np.mean(peaks_b) > np.mean(peaks_l)
