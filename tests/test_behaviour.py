"""Behavioural layer tests: announcement, rescue, and integration in the closed loop."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.behaviour import BehaviourConfig, Person, weekly_factors
from runner.closed_loop import (
    AlgorithmProfile, Scenario, adaptive_profile_schedule, init_bridge, run_closed_loop,
)
from simcore.params import load_subject


class FakeBridge:
    def __init__(self):
        self.requests: list[dict] = []

    def request(self, payload: dict) -> dict:
        self.requests.append(payload)
        if payload["cmd"] == "init":
            return {"ok": True}
        return {
            "glucose": {
                "glucose": {
                    "glucose": 100.0,
                    "delta": 0.0,
                    "shortAvgDelta": 0.0,
                    "longAvgDelta": 0.0,
                    "date": payload["now"],
                    "duraISFminutes": 0.0,
                    "duraISFaverage": 100.0,
                    "parabolaMinutes": 0.0,
                    "bgAcceleration": 0.0,
                    "a0": 100.0,
                    "a1": 0.0,
                    "a2": 0.0,
                    "corrSqu": 0.0,
                }
            },
            "iob": {"iob": [{"iob": 0.0}]},
            "algorithm": None,
            "meal": {
                "carbs": 0.0, "mealCOB": 0.0,
                "slopeFromMaxDeviation": 0.0, "slopeFromMinDeviation": 0.0,
                "usedMinCarbsImpact": 0.0, "lastBolusTime": 0, "lastCarbTime": 0,
            },
            "rt": {"rate": None, "duration": None, "units": 0.0, "insulinReq": 0.0, "reason": ""},
        }


def test_announcement_probability_zero_means_unannounced():
    person = Person(BehaviourConfig(p_announce_main=0.0), np.random.default_rng(1))
    assert [person.announce(50.0) for _ in range(20)] == [0.0] * 20
    assert person.announced_meals == 0
    assert person.unannounced_meals == 20


def test_announcement_probability_one_keeps_the_meal():
    person = Person(BehaviourConfig(p_announce_main=1.0, carb_estimate_cv=0.0), np.random.default_rng(1))
    assert person.announce(50.0) == 50.0
    assert person.announced_meals == 1
    assert person.unannounced_meals == 0


def test_announcement_estimate_is_lognormal_and_bounded():
    person = Person(BehaviourConfig(p_announce_main=1.0, carb_estimate_cv=0.25), np.random.default_rng(7))
    estimates = [person.announce(60.0) for _ in range(200)]
    assert all(value > 0 for value in estimates)
    assert 40.0 < float(np.median(estimates)) < 90.0


def test_rescue_threshold_refractory_and_bounds():
    person = Person(BehaviourConfig(), np.random.default_rng(3))
    assert person.rescue(120.0, 0.0) == 0.0
    first = person.rescue(60.0, 0.0)
    assert 4.0 <= first <= 90.0
    assert person.rescue(60.0, 10.0) == 0.0
    assert person.rescue(60.0, 50.0) > 0.0


def test_rescue_is_deterministic():
    cfg = BehaviourConfig()
    a = Person(cfg, np.random.default_rng(42))
    b = Person(cfg, np.random.default_rng(42))
    assert [a.rescue(65.0, minute) for minute in (0.0, 60.0, 120.0)] == [
        b.rescue(65.0, minute) for minute in (0.0, 60.0, 120.0)
    ]


def test_behaviour_loop_skips_init_carbs_and_logs_rescue():
    subject = load_subject("adult#001")
    profile = AlgorithmProfile(
        sens=50.0, carb_ratio=10.0, target_bg=110.0, current_basal=10.0, enable_auto_isf=False,
    )
    scenario = Scenario(
        duration_min=6 * 60,
        meals=[(60.0, 50.0)],
        meal_mode="announced",
        behaviour=BehaviourConfig(p_announce_main=0.0, rescue_threshold_mgdl=300.0),
    )
    bridge = FakeBridge()
    init_bridge(
        bridge, subject, {},
        {"label": "x", "endTime": 8 * 3600 * 1000, "peakTime": 60 * 60 * 1000, "concentration": 1.0},
        profile, scenario,
    )
    assert not any(item["kind"] == "carbs" for item in bridge.requests[0]["treatments"])
    loop = run_closed_loop(subject, bridge, profile, scenario, seed=1, noise_kind="none")
    assert loop.announced_meals == 0
    assert loop.unannounced_meals == 1
    assert loop.rescue_events
    cycle = next(item for item in bridge.requests if item["cmd"] == "cycle")
    assert not any(item["kind"] == "carbs" for item in cycle["treatments"])


def test_snack_probability_uses_the_snack_rate():
    person = Person(
        BehaviourConfig(p_announce_main=1.0, p_announce_snack=0.0),
        np.random.default_rng(5),
    )
    assert person.announce(20.0, is_snack=True) == 0.0
    assert person.announce(50.0, is_snack=False) > 0.0


def test_weekly_factors_are_centred_and_deterministic():
    a = weekly_factors(8, isf_cv=0.058, seed=11)
    b = weekly_factors(8, isf_cv=0.058, seed=11)
    assert a == b
    assert len(a) == 8
    assert all(isf > 0 and cr > 0 for isf, cr in a)
    assert 0.8 < float(np.median([isf for isf, _ in a])) < 1.2


def test_adaptive_profile_schedule_starts_after_a_week():
    profile = AlgorithmProfile(sens=50.0, carb_ratio=10.0, target_bg=110.0, current_basal=1.0)
    assert adaptive_profile_schedule(profile, days=1) == []
    schedule = adaptive_profile_schedule(profile, days=14, seed=3)
    assert len(schedule) == 2
    assert schedule[0][0] == 7 * 24 * 60.0
    assert schedule[0][1].sens > 0 and schedule[0][1].carb_ratio > 0
