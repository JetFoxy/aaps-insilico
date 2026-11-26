"""Behavioural layer: imperfect meal announcement and rescue carbohydrate.

The parameters follow `timsim/person/behaviour.py` (MIT), which sources the announcement
probabilities and carbohydrate-counting error to Brazeau et al. (2013) and Roversi et al.
(2020), and the rescue-entry distribution to the DCLP3 closed-loop cohort. This is a stress
layer, not a re-derivation of those studies: page-level provenance is TODO before publication.

The layer is deliberately optional. With ``behaviour=None`` the closed loop keeps its previous
behaviour exactly, so existing acceptance runs remain comparable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BehaviourConfig:
    """Probabilities and distributions for one person's behaviour."""

    p_announce_main: float = 0.80
    p_announce_snack: float = 0.35
    carb_estimate_cv: float = 0.25
    carb_estimate_bias: float = 1.0
    rescue: bool = True
    rescue_threshold_mgdl: float = 70.0
    rescue_refractory_min: float = 45.0
    rescue_delay_min: tuple[float, float] = (4.0, 16.0)
    rescue_dose_median_g: float = 15.0
    rescue_dose_per_mgdl: float = -0.117
    rescue_dose_sigma: float = 0.857
    rescue_dose_bounds: tuple[float, float] = (4.0, 90.0)
    rescue_bioavailability: float = 0.83


class Person:
    """Announcement and rescue decisions for one run, driven by a seeded generator."""

    def __init__(self, config: BehaviourConfig, rng: np.random.Generator):
        self.config = config
        self.rng = rng
        self.last_rescue_min = -1e9
        self.announced_meals = 0
        self.unannounced_meals = 0

    def announce(self, grams: float, is_snack: bool = False) -> float:
        """What the algorithm is told about a meal of ``grams``; zero means unannounced."""
        if grams <= 0:
            return 0.0
        u = float(self.rng.random())
        sigma = float(np.sqrt(np.log(1.0 + self.config.carb_estimate_cv ** 2)))
        estimate = (
            grams
            * self.config.carb_estimate_bias
            * float(self.rng.lognormal(-0.5 * sigma ** 2, sigma))
        )
        probability = (
            self.config.p_announce_snack if is_snack else self.config.p_announce_main
        )
        if u > probability:
            self.unannounced_meals += 1
            return 0.0
        self.announced_meals += 1
        return estimate

    def rescue(self, cgm: float, minute: float) -> float:
        """Grams of fast carbohydrate eaten for a low, or zero when no rescue happens."""
        cfg = self.config
        if not cfg.rescue or cgm > cfg.rescue_threshold_mgdl:
            return 0.0
        if minute - self.last_rescue_min < cfg.rescue_refractory_min:
            return 0.0
        median = max(
            cfg.rescue_dose_median_g + cfg.rescue_dose_per_mgdl * (float(cgm) - 65.0),
            cfg.rescue_dose_bounds[0],
        )
        grams = float(self.rng.lognormal(float(np.log(median)), cfg.rescue_dose_sigma))
        grams = float(np.clip(grams, *cfg.rescue_dose_bounds))
        self.last_rescue_min = float(minute)
        return grams

    def rescue_delay(self) -> float:
        low, high = self.config.rescue_delay_min
        return float(self.rng.uniform(low, high))


def weekly_factors(
    weeks: int,
    isf_cv: float = 0.15,
    cr_cv: float = 0.0,
    seed: int = 0,
) -> list[tuple[float, float]]:
    """Multiplicative weekly drift factors for the algorithm's ISF and carb ratio.

    The default 15 % sits inside the measured 8-22 % CV of real weekly ISF drift (TimSim,
    ``timsim/person/behaviour.py``: "Real weekly ISF drift is 8-22% CV, measured on the
    cohort"). TimSim's 5.8 % figure is the controller-derived drift *reading*, not the
    person, and is not used here. CR drift has no comparable measurement, so its default is
    zero. The factors are centred on one and deterministic for a seed.
    """
    rng = np.random.default_rng(seed)
    out: list[tuple[float, float]] = []
    for _ in range(max(0, weeks)):
        sigma_isf = float(np.sqrt(np.log(1.0 + isf_cv ** 2)))
        sigma_cr = float(np.sqrt(np.log(1.0 + cr_cv ** 2)))
        out.append((
            float(rng.lognormal(-0.5 * sigma_isf ** 2, sigma_isf)),
            float(rng.lognormal(-0.5 * sigma_cr ** 2, sigma_cr)),
        ))
    return out
