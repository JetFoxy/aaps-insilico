"""DynISF ISF command: profile-sens mode returns the profile ISF at target and falls with BG."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from bridge.client import Bridge, DEFAULT_CLASSPATH, insulin_from_settings, load_prefs

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not DEFAULT_CLASSPATH.exists(), reason="bridge classpath not built")


def _sens(bridge, now: int, bg: float, isf: float = 50.0) -> float:
    readings = [[now - 5 * 60_000 * i, bg] for i in range(12, 0, -1)]
    return float(bridge.request({
        "cmd": "dynisf", "now": now, "readings": readings, "sourceSensor": "Libre2",
        "isf": isf, "carbRatio": 10.0, "target": 100.0, "currentBasal": 1.0, "tdd": 40.0,
    })["sens"])


def test_dynisf_returns_profile_isf_at_target() -> None:
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    now = 1_790_812_800_000 + 10 * 3_600_000
    with Bridge() as bridge:
        bridge.request({
            "cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin, "treatments": [],
            "basal": [[1_790_812_800_000, 1.0]], "timezone": "Europe/Moscow",
        })
        assert _sens(bridge, now, 100.0) == pytest.approx(50.0, rel=1e-6)
        high = _sens(bridge, now, 200.0)
        assert high < 50.0
        assert _sens(bridge, now, 250.0) < high


def test_dynisf_loop_arm_runs() -> None:
    import numpy as np

    from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
    from simcore.params import load_subject
    from simcore.population import compute_cr, compute_tdi

    subject = load_subject("adult#001")
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    profile = AlgorithmProfile(
        sens=30.0, carb_ratio=cr, target_bg=110.0,
        current_basal=subject.basal_u_per_min * 60, algorithm="dynisf", tdd=tdi,
    )
    scenario = Scenario(duration_min=120, meals=[(60.0, 30.0)], meal_mode="fcl")
    residuals = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")
    with Bridge() as bridge:
        init_bridge(bridge, subject, prefs, insulin, profile, scenario)
        loop = run_closed_loop(subject, bridge, profile, scenario, seed=1, residuals=residuals)
    assert len(loop.cgm) == 24
    assert all(value > 0 for value in loop.variable_sens)
    assert min(loop.variable_sens) < 30.0


def _readings(now: int, bg: float) -> list[list[float]]:
    return [[now - 5 * 60_000 * index, bg] for index in range(12, 0, -1)]


def _dynisf(bridge, now: int, tdd: float) -> float:
    return float(bridge.request({
        "cmd": "dynisf", "now": now, "readings": _readings(now, 180.0),
        "sourceSensor": "Libre2", "isf": 50.0, "carbRatio": 10.0,
        "target": 100.0, "currentBasal": 1.0, "tdd": tdd,
    })["sens"])


def test_dynisf_runner_cache_survives_same_tdd_and_resets_on_init() -> None:
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    prefs["dynisf_use_profile_sens"] = False
    prefs["use_dynamic_sensitivity"] = True
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    now = 1_790_812_800_000 + 10 * 3_600_000
    init = {
        "cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin,
        "treatments": [], "basal": [[1_790_812_800_000, 1.0]],
        "timezone": "Europe/Moscow",
    }
    with Bridge() as bridge:
        bridge.request(init)
        first = _dynisf(bridge, now, 40.0)
        iob = bridge.request({"cmd": "iob", "now": now, "treatments": []})["iob"]
        bridge.request({
            "cmd": "stepSmb", "input": {
                "now": now, "TDD": 40.0,
                "glucoseStatus": {"glucose": 180.0, "noise": 0.0, "delta": 2.0,
                                  "shortAvgDelta": 2.0, "longAvgDelta": 2.0, "date": now},
                "currentTemp": {"duration": 0, "rate": 0.0, "minutesrunning": 0},
                "iob": iob, "autosens": {"ratio": 1.0}, "meal": {},
                "profile": {"sens": 50.0, "variable_sens": first, "carb_ratio": 10.0,
                            "target_bg": 100.0, "current_basal": 1.0},
                "microBolusAllowed": False, "flatBGs": False, "smbRatio": 0.3,
            },
        })
        assert _dynisf(bridge, now, 40.0) == pytest.approx(first)
        changed = _dynisf(bridge, now, 80.0)
        assert changed != pytest.approx(first)

        bridge.request(init)
        assert _dynisf(bridge, now, 40.0) == pytest.approx(first)
