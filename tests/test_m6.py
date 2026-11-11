"""M6 acceptance tests: pump quantization/limits, open loop = M1, determinism, bridge latency."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from pump.pump import Pump, PumpConfig, quantize
from runner.open_loop import run_open_loop
from simcore.model import Bolus, Meal, Schedule, simulate
from simcore.params import load_subject

from bridge.client import DEFAULT_CLASSPATH

CLASS_PATH = DEFAULT_CLASSPATH


def test_quantization_rounds_to_pump_steps() -> None:
    assert quantize(0.123, 0.05) == pytest.approx(0.1)
    assert quantize(0.18, 0.05) == pytest.approx(0.2)
    assert quantize(0.678, 0.01) == pytest.approx(0.68)


def test_pump_limits_and_reservoir() -> None:
    pump = Pump(PumpConfig(max_bolus=5.0, reservoir_units=3.0))
    assert pump.bolus(10.0) == pytest.approx(3.0)
    assert pump.remaining_units == pytest.approx(0.0)
    assert pump.temp_basal(10.0) == pytest.approx(4.0)


def test_open_loop_without_quantization_equals_m1_core() -> None:
    subject = load_subject("adult#001")
    meals = [Meal(7 * 60, 60.0)]
    boluses = [(7 * 60, 60.0 / 10.5)]
    open_loop = run_open_loop(subject, subject.basal_u_per_min * 60, meals, boluses, 12 * 60, 1.0, smooth=False)
    direct = simulate(
        subject,
        Schedule(subject.basal_u_per_min, meals, [Bolus(t, u) for t, u in boluses]),
        12 * 60,
        1.0,
    )
    assert np.allclose(open_loop.trace.glucose, direct.glucose, rtol=0.0, atol=1e-9)


def test_open_loop_quantization_changes_insulin_input() -> None:
    subject = load_subject("adult#001")
    result = run_open_loop(subject, subject.basal_u_per_min * 60, [], [(60.0, 1.03)], 6 * 60, 1.0, smooth=True)
    assert result.schedule.boluses[0].units == pytest.approx(1.05)


def test_open_loop_is_deterministic() -> None:
    subject = load_subject("adult#001")
    meals = [Meal(7 * 60, 60.0)]
    boluses = [(7 * 60, 60.0 / 10.5)]
    first = run_open_loop(subject, subject.basal_u_per_min * 60, meals, boluses, 6 * 60, 1.0)
    second = run_open_loop(subject, subject.basal_u_per_min * 60, meals, boluses, 6 * 60, 1.0)
    assert np.array_equal(first.trace.glucose, second.trace.glucose)


@pytest.mark.skipif(not CLASS_PATH.exists(), reason="bridge classpath not built")
def test_bridge_selftest_is_deterministic_and_measured() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
    from bridge.client import Bridge

    with Bridge() as bridge:
        first = bridge.request({"cmd": "selftest"})
        second = bridge.request({"cmd": "selftest"})
        assert first == second
        start = time.perf_counter()
        for _ in range(20):
            bridge.request({"cmd": "selftest"})
        per_call = (time.perf_counter() - start) / 20
    print(f"bridge step latency {per_call * 1000:.1f} ms")
    assert per_call < 0.5


@pytest.mark.skipif(not CLASS_PATH.exists(), reason="bridge classpath not built")
def test_closed_loop_is_deterministic() -> None:
    from bridge.client import Bridge, load_prefs, insulin_from_settings
    from runner.closed_loop import AlgorithmProfile, Scenario, run_closed_loop

    subject = load_subject("adult#001")
    prefs = load_prefs(Path(__file__).resolve().parents[1] / "data/raw/settings.json")
    insulin = insulin_from_settings(Path(__file__).resolve().parents[1] / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    residuals = np.load(Path(__file__).resolve().parent / "fixtures/cgm_residuals.npy")
    profile = AlgorithmProfile(
        sens=50.0, carb_ratio=10.5, target_bg=110.0,
        max_iob=12.0, max_basal=4.0, current_basal=subject.basal_u_per_min * 60,
    )

    def run_once() -> tuple[list[float], list[float]]:
        with Bridge() as bridge:
            bridge.request({
                "cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin,
                "treatments": [], "basal": [[0, subject.basal_u_per_min * 60]], "timezone": "Europe/Moscow",
            })
            result = run_closed_loop(subject, bridge, profile, Scenario(duration_min=60), seed=3, residuals=residuals)
        return list(result.rate), list(result.cgm)

    first_rates, first_cgm = run_once()
    second_rates, second_cgm = run_once()
    print(f"rates {first_rates[:6]} ...")
    assert first_rates == second_rates
    assert first_cgm == second_cgm
    assert any(abs(rate - profile.current_basal) > 1e-9 for rate in first_rates)
