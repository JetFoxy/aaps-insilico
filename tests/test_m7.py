"""M7 acceptance tests: metrics, meal modes, failure injection, determinism."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from bridge.client import Bridge, BridgeError, insulin_from_settings, load_prefs
from runner.closed_loop import EPOCH0_MS, AlgorithmProfile, Scenario, init_bridge, run_closed_loop
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


def _run(subject, scenario, seed=1, pump_config=None, on_cycle=None, bridge_factory=Bridge):
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    profile = _profile(subject)
    residuals = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")
    with bridge_factory() as bridge:
        init_bridge(bridge, subject, prefs, insulin, profile, scenario)
        return run_closed_loop(
            subject, bridge, profile, scenario, seed=seed, residuals=residuals,
            pump_config=pump_config, on_cycle=on_cycle,
        )


def test_batch_loop_uses_one_cycle_request_per_cycle() -> None:
    requests = []

    class CountingBridge(Bridge):
        def request(self, payload):
            requests.append(payload["cmd"])
            return super().request(payload)

    subject = load_subject("adult#001")
    scenario = Scenario(duration_min=30, meals=[(15.0, 30.0)])
    _run(subject, scenario, bridge_factory=CountingBridge)
    assert requests == ["init"] + ["cycle"] * 6


def test_reused_bridge_does_not_leak_loop_state() -> None:
    subject = load_subject("adult#001")
    scenario = Scenario(duration_min=30, meals=[(15.0, 30.0)])
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    profile = AlgorithmProfile(
        sens=50.0, carb_ratio=10.5, target_bg=110.0,
        current_basal=subject.basal_u_per_min * 60, enable_auto_isf=True,
    )
    residuals = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")
    def fresh():
        with Bridge() as bridge:
            init_bridge(bridge, subject, prefs, insulin, profile, scenario)
            return run_closed_loop(
                subject, bridge, profile, scenario, seed=7, residuals=residuals,
            )

    first = fresh()
    second = fresh()
    with Bridge() as bridge:
        shared = []
        for _ in range(2):
            init_bridge(bridge, subject, prefs, insulin, profile, scenario)
            shared.append(run_closed_loop(
                subject, bridge, profile, scenario, seed=7, residuals=residuals,
            ))

    assert shared[0].cgm == first.cgm
    assert shared[0].rate == first.rate
    assert shared[1].cgm == second.cgm
    assert shared[1].rate == second.rate


def test_cycle_rejects_unknown_algorithm_before_stateful_calls() -> None:
    subject = load_subject("adult#001")
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    profile = _profile(subject)
    scenario = Scenario(duration_min=5)
    now = EPOCH0_MS + 5 * 60_000
    current_temp = {"duration": 0, "rate": profile.current_basal, "minutesrunning": 0}

    def cycle_payload(algorithm: str) -> dict:
        return {
            "cmd": "cycle", "now": now,
            "readings": [[now - 10 * 60_000, 95.0], [now - 5 * 60_000, 100.0], [now, 105.0]],
            "sourceSensor": "Libre2", "autosens": 1.0,
            "exerciseMode": False, "halfBasalExerciseTarget": 160,
            "isTempTarget": False, "treatments": [], "mealMode": scenario.meal_mode,
            "isf": profile.sens, "carbRatio": profile.carb_ratio,
            "target": profile.target_bg, "targetLow": profile.target_bg,
            "targetHigh": profile.target_bg, "currentBasal": profile.current_basal,
            "tdd": profile.tdd, "algorithm": algorithm,
            "input": {
                "now": now, "TDD": profile.tdd, "currentTemp": current_temp,
                "autosens": {"ratio": 1.0},
                "profile": {
                    "sens": profile.sens, "variable_sens": profile.sens,
                    "carb_ratio": profile.carb_ratio, "target_bg": profile.target_bg,
                    "min_bg": profile.target_bg, "max_bg": profile.target_bg,
                    "current_basal": profile.current_basal, "max_iob": profile.max_iob,
                    "max_basal": profile.max_basal, "SMBInterval": profile.smb_interval,
                    "bolus_increment": profile.bolus_increment,
                    "enable_autoISF": True, "out_units": "mg/dl",
                    "temptargetSet": False, "TDD": profile.tdd,
                    "insulinDivisor": 75, "use_TDD_for_predictions": True,
                },
                "microBolusAllowed": True, "flatBGs": False, "autoIsfMode": True,
                "loopWantedSmb": "AAPS", "smbRatio": 0.5,
                "smbMaxRangeExtension": 1.0, "iobThresholdPercent": 100,
                "profilePercentage": 100, "activityConsoleLog": "",
            },
        }

    def normalized_cycle(bridge: Bridge) -> dict:
        response = bridge.request(cycle_payload("autoisf"))
        response["glucose"].pop("debug", None)
        return response

    with Bridge() as bridge:
        init_bridge(bridge, subject, prefs, insulin, profile, scenario)
        invalid = cycle_payload("not-supported")
        with pytest.raises(BridgeError, match="unknown algorithm"):
            bridge.request(invalid)
        actual = normalized_cycle(bridge)

    with Bridge() as bridge:
        init_bridge(bridge, subject, prefs, insulin, profile, scenario)
        expected = normalized_cycle(bridge)

    assert actual == expected


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


def test_announced_amount_error_increases_cob() -> None:
    subject = load_subject("adult#001")
    base = _run(subject, Scenario(duration_min=180, meals=[(60.0, 50.0)], meal_mode="announced"))
    over = _run(subject, Scenario(
        duration_min=180, meals=[(60.0, 50.0)], meal_mode="announced",
        announce_amount_error_frac=0.2,
    ))
    assert max(over.cob) > max(base.cob) * 1.1


def test_announced_lead_starts_cob_earlier() -> None:
    subject = load_subject("adult#001")
    base = _run(subject, Scenario(duration_min=180, meals=[(60.0, 50.0)], meal_mode="announced"))
    lead = _run(subject, Scenario(
        duration_min=180, meals=[(60.0, 50.0)], meal_mode="announced",
        announce_lead_min=30.0,
    ))
    first_base = next(index for index, cob in enumerate(base.cob) if cob > 0)
    first_lead = next(index for index, cob in enumerate(lead.cob) if cob > 0)
    assert first_lead < first_base
