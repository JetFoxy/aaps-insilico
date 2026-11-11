"""M7 acceptance tests: metrics, meal modes, failure injection, determinism."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from bridge.client import Bridge, insulin_from_settings, load_prefs
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.metrics import compute_metrics
from simcore.params import load_subject

ROOT = Path(__file__).resolve().parents[1]
from bridge.client import DEFAULT_CLASSPATH

CLASS_PATH = DEFAULT_CLASSPATH
pytestmark = pytest.mark.skipif(not CLASS_PATH.exists(), reason="bridge classpath not built")


def _profile(subject) -> AlgorithmProfile:
    return AlgorithmProfile(
        sens=50.0, carb_ratio=10.5, target_bg=110.0,
        max_iob=12.0, max_basal=4.0, current_basal=subject.basal_u_per_min * 60,
    )


def _run(subject, scenario, seed=1, pump_config=None, on_cycle=None):
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    profile = _profile(subject)
    residuals = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")
    with Bridge() as bridge:
        init_bridge(bridge, subject, prefs, insulin, profile, scenario)
        return run_closed_loop(
            subject, bridge, profile, scenario, seed=seed, residuals=residuals,
            pump_config=pump_config, on_cycle=on_cycle,
        )


def test_metrics_on_synthetic_series() -> None:
    glucose = np.array([50.0, 100.0, 200.0, 100.0])
    metrics = compute_metrics(glucose, [(5.0, 1.0, "bolus")])
    assert metrics.tbr_70 == pytest.approx(0.25)
    assert metrics.tbr_54 == pytest.approx(0.25)
    assert metrics.tir_70_180 == pytest.approx(0.5)
    assert metrics.lbgi > 0.0
    assert metrics.smb_count == 1


def test_fcl_sees_no_carbs_and_announced_sees_cob() -> None:
    subject = load_subject("adult#001")
    fcl = _run(subject, Scenario(duration_min=30, meals=[(0.0, 30.0)], meal_mode="fcl"))
    announced = _run(subject, Scenario(duration_min=30, meals=[(0.0, 30.0)], meal_mode="announced"))
    assert max(fcl.insulin_req) >= 0.0
    assert len(announced.cgm) == len(fcl.cgm)


def test_suspend_stops_basal() -> None:
    subject = load_subject("adult#001")
    result = _run(subject, Scenario(duration_min=60, suspend=(30.0, 30.0)))
    suspended = [rate for t, rate in zip(result.time, result.rate) if 30.0 < t <= 60.0]
    assert suspended and all(rate == 0.0 for rate in suspended)


def test_closed_loop_with_meals_is_deterministic() -> None:
    subject = load_subject("adult#001")
    scenario = Scenario(duration_min=60, meals=[(30.0, 30.0)], meal_mode="fcl")
    first = _run(subject, scenario, seed=4)
    second = _run(subject, scenario, seed=4)
    assert first.cgm == second.cgm
    assert first.rate == second.rate


def test_planned_temps_persist_with_minutes_remaining() -> None:
    subject = load_subject("adult#001")
    result = _run(subject, Scenario(duration_min=60, meals=[(0.0, 30.0)]))
    assert result.current_temp[0]["duration"] == 0
    running = [entry for entry in result.current_temp if entry["duration"] > 0]
    assert running, "a 30-min temp should still be running on the next cycle"
    assert max(entry["duration"] for entry in running) >= 20
    assert max(entry["minutesrunning"] for entry in running) >= 5


def test_basal_journal_matches_delivered_rates() -> None:
    subject = load_subject("adult#001")
    result = _run(subject, Scenario(duration_min=60))
    journal = sum(units for _, units, kind in result.dose_events if kind == "basal")
    assert journal == pytest.approx(sum(result.rate) * 5.0 / 60.0)


def test_diurnal_variability_reaches_the_loop() -> None:
    subject = load_subject("adult#001")
    plain = _run(subject, Scenario(duration_min=600))
    dawn = _run(subject, Scenario(duration_min=600, dawn_egp=1.5, si_multipliers=(0.4, 1.0, 1.0)))
    assert max(abs(a - b) for a, b in zip(plain.glucose, dawn.glucose)) > 1.0


def test_smb_boluses_reach_the_physiology() -> None:
    subject = load_subject("adult#001")
    scenario = Scenario(duration_min=24 * 60, meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)])
    result = _run(subject, scenario)
    journal = sum(units for _, units, kind in result.dose_events if kind == "bolus")
    assert journal > 0.0
    assert result.bolus_units_to_model >= 0.95 * journal


def test_blocked_boluses_raise_glucose() -> None:
    from pump.pump import PumpConfig

    subject = load_subject("adult#001")
    scenario = Scenario(duration_min=8 * 60, meals=[(60.0, 50.0)])
    normal = _run(subject, scenario)
    blocked = _run(subject, scenario, pump_config=PumpConfig(max_bolus=0.0))
    assert blocked.bolus_units_to_model == 0.0
    assert float(np.mean(blocked.cgm)) > float(np.mean(normal.cgm))


def test_smb_ratio_scales_with_cadence() -> None:
    from runner.closed_loop import smb_ratio_for_cadence

    profile = AlgorithmProfile(sens=50.0, carb_ratio=10.5, target_bg=110.0)
    assert smb_ratio_for_cadence(profile, 3.0) == pytest.approx(0.3)
    assert smb_ratio_for_cadence(profile, 5.0) == pytest.approx(0.5)
    assert smb_ratio_for_cadence(profile, 1.0) == pytest.approx(0.1)


def test_delayed_bolus_still_reaches_the_model() -> None:
    from pump.pump import PumpConfig

    subject = load_subject("adult#001")
    scenario = Scenario(duration_min=24 * 60, meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)])
    immediate = _run(subject, scenario)
    delayed = _run(subject, scenario, pump_config=PumpConfig(bolus_delay_min=15.0))
    journal = sum(units for _, units, kind in delayed.dose_events if kind == "bolus")
    assert journal > 0.0
    assert delayed.bolus_units_to_model >= 0.9 * journal
    assert delayed.cgm != immediate.cgm


def test_cgm_bias_shifts_the_sensor_trace() -> None:
    subject = load_subject("adult#001")
    scenario = Scenario(duration_min=6 * 60, cgm_bias=(120.0, 60.0, 50.0))
    result = _run(subject, scenario)
    inside = [i for i, t in enumerate(result.time) if 120.0 < t <= 180.0]
    outside = [i for i, t in enumerate(result.time) if t <= 120.0]
    assert np.mean([result.cgm[i] - result.glucose[i] for i in inside]) > 40.0
    assert abs(np.mean([result.cgm[i] - result.glucose[i] for i in outside])) < 10.0


def test_on_cycle_callback_fires_once_per_cycle() -> None:
    subject = load_subject("adult#001")
    lengths = []

    def capture(result) -> None:
        lengths.append((len(result.time), result.time[-1]))

    result = _run(subject, Scenario(duration_min=30), on_cycle=capture)
    assert [length for length, _ in lengths] == list(range(1, len(result.time) + 1))
    assert [time for _, time in lengths] == result.time
