"""M8 acceptance tests: second model (Deichmann 2023) direction checks against the S2017 core."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from exercise.model import ExerciseSession
from second_model.deichmann import run_deichmann
from simcore.model import Bolus, Meal, Schedule, TimeVarying, simulate
from simcore.params import load_subject

MEALS_MG = {60: 40000, 420: 60000, 780: 50000}
MEALS_G = [(60, 40.0), (420, 60.0), (780, 50.0)]


def _deichmann(ac: float) -> np.ndarray:
    counts = np.zeros(1441)
    if ac > 0:
        counts[240:300] = ac
    return run_deichmann(counts, MEALS_MG)


def _core(intensity: float) -> np.ndarray:
    subject = load_subject("adult#001")
    exercise = ExerciseSession(start_min=240, duration_min=60, intensity=intensity) if intensity > 0 else None
    schedule = Schedule(
        basal_u_per_min=subject.basal_u_per_min,
        meals=[Meal(t, g) for t, g in MEALS_G],
        boluses=[Bolus(t, g / 17.0) for t, g in MEALS_G],
    )
    return simulate(subject, schedule, 1441, 1.0, TimeVarying(exercise=exercise)).glucose


def test_deichmann_meal_raises_glucose() -> None:
    glucose = _deichmann(0.0)
    assert glucose[0] < 130.0
    assert glucose.max() > 150.0


def test_moderate_exercise_lowers_glucose_in_both_models() -> None:
    rest = _deichmann(0.0)
    moderate = _deichmann(4317.0)
    assert moderate[300] < rest[300]
    core_rest = _core(0.0)
    core_moderate = _core(0.6)
    assert core_moderate[300] < core_rest[300]


def test_high_intensity_direction_differs_and_is_explained() -> None:
    rest = _deichmann(0.0)
    high = _deichmann(6539.0)
    assert high[300] > rest[300], "Deichmann: high-intensity exercise raises glucose"
    core_rest = _core(0.0)
    core_high = _core(0.9)
    assert core_high[300] < core_rest[300], "core lacks the GP rise and falls at high intensity"
