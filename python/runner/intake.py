"""Realistic eating occasions: seven template rows with timing, size and probability.

The template follows TimSim (MIT, ``timsim/simulation/run.py``), whose rows were regrounded on
292,140 carbohydrate entries from 147 OpenAPS Data Commons users over 48,454 user-days. It
reproduces the measured share of grams in each window of the day to within 0.7 percentage
points, at 5.2 eating occasions a day against a logged 5.56. Total intake is held near 192 g/day
at 70 kg rather than the logged median of 162, because logged grams under-state eaten grams.

This is usage data, not a glucose outcome: no reference-population trace was consulted. TimSim
keeps the template's 00:36 snack clipped to 00:30, so a small pile-up sits at the clip.
"""
from __future__ import annotations

import numpy as np

DEFAULT_MEALS = [
    (8.4, 1.2, 36, 13, 0.90, False),
    (11.0, 0.9, 10, 5, 0.45, True),
    (12.8, 1.0, 54, 18, 0.95, False),
    (16.2, 1.1, 29, 13, 0.80, True),
    (19.0, 0.9, 52, 19, 0.95, False),
    (22.1, 1.2, 32, 14, 0.70, True),
    (0.6, 1.5, 20, 11, 0.45, True),
]

INTAKE_SEED_SALT = 0x5EED


def draw_meals(days: int, rng: np.random.Generator, body_weight: float, template=None):
    """A meal schedule with realistic scatter in timing, size and whether it happens.

    Returns ``[(hours_from_start, grams, is_snack), ...]`` sorted by time.
    """
    template = template or DEFAULT_MEALS
    scale = float(np.clip(float(body_weight) / 70.0, 0.5, 1.15))
    out = []
    for day in range(max(1, int(days))):
        for hour, hour_sd, grams, gram_sd, probability, snack in template:
            if rng.random() > probability:
                continue
            h = float(np.clip(hour + rng.normal(0.0, hour_sd), 0.5, 23.5))
            g = max(3.0, rng.normal(grams, gram_sd) * scale)
            out.append((day * 24.0 + h, float(round(g)), bool(snack)))
    return sorted(out)


def intake_schedule(days: int, seed: int, body_weight: float, template=None):
    """``(meals, snacks)`` as ``(minute_from_start, grams)``, deterministic for a seed."""
    meals: list[tuple[int, int]] = []
    snacks: list[tuple[int, int]] = []
    drawn = draw_meals(days, np.random.default_rng(int(seed)), body_weight, template)
    for hours, grams, snack in drawn:
        target = snacks if snack else meals
        target.append((int(round(hours * 60.0)), int(grams)))
    return meals, snacks
