"""Diurnal insulin-sensitivity classes and dawn, from published sources.

Visentin R, et al. Circadian Variability of Insulin Sensitivity: Physiological Input for In Silico
Artificial Pancreas. Diabetes Technol Ther. 2015;17(1):1-7 (PMC4290795):
  seven classes h/l at breakfast/lunch/dinner, probabilities 0.1/0.05/0.05/0.1/0.2/0.2/0.3,
  low = 0.4 * high, multiplicative noise N(1, 0.2^2), transitions at 4:00, 11:00, 17:00.

Mallad et al. (via Visentin 2018, section Dawn): EGP rises by about 1.5 mg/kg/min and glucose by
about 30 mg/dL between 3:00 and 7:00. Only the EGP magnitude is used here; the `kir` decrease is
left at 1.0 because its magnitude is not published.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

ALPHA = 0.4
NOISE_SIGMA = 0.2

CLASSES: list[tuple[str, tuple[int, int, int]]] = [
    ("hhh", (1, 1, 1)),
    ("hhl", (1, 1, 0)),
    ("hlh", (1, 0, 1)),
    ("hll", (1, 0, 0)),
    ("lhh", (0, 1, 1)),
    ("lhl", (0, 1, 0)),
    ("llh", (0, 0, 1)),
]
PROBABILITIES = (0.1, 0.05, 0.05, 0.1, 0.2, 0.2, 0.3)


@dataclass(frozen=True)
class SIDay:
    name: str
    multipliers: tuple[float, float, float]


def assign_class(seed: int) -> str:
    rng = random.Random(seed)
    index = rng.choices(range(len(CLASSES)), weights=PROBABILITIES, k=1)[0]
    return CLASSES[index][0]


def si_day(seed: int) -> SIDay:
    """One subject's nominal class plus multiplicative noise by seed (deterministic)."""
    rng = random.Random(seed)
    index = rng.choices(range(len(CLASSES)), weights=PROBABILITIES, k=1)[0]
    name, pattern = CLASSES[index]
    multipliers = tuple(
        (1.0 if high else ALPHA) * max(0.5, min(1.5, rng.gauss(1.0, NOISE_SIGMA)))
        for high in pattern
    )
    return SIDay(name=name, multipliers=multipliers)
