"""M5 acceptance tests: exercise SI profile, recovery tail, ka effect, hypo risk by duration."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from exercise.model import KA_MULTIPLIER, ExerciseSession
from simcore.model import Bolus, Meal, Schedule, TimeVarying, simulate
from simcore.params import load_subject


def test_si_profile_and_average_doubling() -> None:
    session = ExerciseSession(start_min=120.0, duration_min=75.0)
    assert session.si_multiplier(0.0) == 1.0
    assert session.si_multiplier(120.0) == 3.29
    assert abs(session.si_multiplier(195.0 + 82.5) - (3.29 + 1.0) / 2) < 1e-9
    assert session.si_multiplier(360.0) == 1.0
    grid = np.linspace(0.0, 360.0, 3601)
    average = float(np.mean([session.si_multiplier(t) for t in grid]))
    print(f"average SI multiplier {average:.2f}")
    assert abs(average - 2.0) < 0.05


def test_intensity_scales_the_peak() -> None:
    session = ExerciseSession(start_min=120.0, duration_min=60.0, intensity=0.5)
    assert abs(session.si_multiplier(150.0) - (1.0 + 2.29 * 0.5)) < 1e-9


def test_ka_multiplier_reproduces_thirty_percent_peak_insulin() -> None:
    subject = load_subject("adult#001")
    schedule = Schedule(subject.basal_u_per_min, [Meal(60, 60)], [Bolus(60, 60 / 10.5)])
    baseline = simulate(subject, schedule, 360, 1.0).insulin_pmol.max()
    with_ka = simulate(
        subject, schedule, 360, 1.0,
        TimeVarying(exercise=ExerciseSession(90, 60, si_gain=1.0), ka_multiplier=KA_MULTIPLIER),
    ).insulin_pmol.max()
    print(f"peak insulin ratio {with_ka / baseline:.3f}")
    assert abs(with_ka / baseline - 1.30) < 0.02


def test_hypo_risk_grows_with_exercise_duration() -> None:
    subject = load_subject("adult#001")
    schedule = Schedule(subject.basal_u_per_min)
    nadirs = []
    for duration in (30.0, 60.0, 90.0):
        session = ExerciseSession(start_min=120.0, duration_min=duration)
        trace = simulate(subject, schedule, 8 * 60, 1.0, TimeVarying(exercise=session))
        nadirs.append(float(trace.glucose.min()))
    print(f"nadirs {nadirs}")
    assert nadirs[0] > nadirs[1] > nadirs[2]
