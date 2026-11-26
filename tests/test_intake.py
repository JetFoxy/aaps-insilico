"""Meal/snack template: sourced scatter, determinism, and per-subject scaling."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.intake import DEFAULT_MEALS, draw_meals, intake_schedule


def test_template_has_seven_occasions_and_four_snacks():
    assert len(DEFAULT_MEALS) == 7
    assert sum(1 for row in DEFAULT_MEALS if row[5]) == 4


def test_draw_meals_is_sorted_and_bounded():
    meals = draw_meals(3, np.random.default_rng(7), body_weight=70.0)
    assert meals == sorted(meals)
    assert all(grams >= 3.0 for _, grams, _ in meals)
    assert all(0.5 <= hour < 24.0 * 3 for hour, _, _ in meals)


def test_draw_meals_is_deterministic_for_a_seed():
    a = draw_meals(2, np.random.default_rng(11), body_weight=70.0)
    b = draw_meals(2, np.random.default_rng(11), body_weight=70.0)
    assert a == b


def test_draw_meals_scales_grams_with_body_weight():
    light = draw_meals(5, np.random.default_rng(3), body_weight=50.0)
    heavy = draw_meals(5, np.random.default_rng(3), body_weight=100.0)
    assert sum(g for _, g, _ in heavy) > sum(g for _, g, _ in light)


def test_intake_schedule_splits_meals_and_snacks_in_minutes():
    meals, snacks = intake_schedule(2, seed=5, body_weight=70.0)
    assert meals == sorted(meals) and snacks == sorted(snacks)
    assert all(0 <= minute < 2 * 24 * 60 for minute, _ in meals + snacks)
    assert intake_schedule(2, seed=5, body_weight=70.0) == (meals, snacks)
