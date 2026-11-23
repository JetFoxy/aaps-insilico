import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.whatif_model import match_subject, trace_scenario


class FakeSubject:
    def __init__(self, name, cr, tdi, isf):
        self.name = name
        self.cr = cr
        self.tdi = tdi
        self.isf = isf


def test_match_subject_picks_nearest_in_log_space():
    subjects = {"a": FakeSubject("a", 10.0, 40.0, 50.0),
                "b": FakeSubject("b", 20.0, 80.0, 100.0)}
    match = match_subject(subjects, tdi=41.0, cr=10.2, isf=51.0)
    assert match.subject_name == "a"
    assert match.confidence == "good"
    assert match.distance == pytest.approx(
        math.sqrt(sum(v * v for v in (math.log(41/40), math.log(10.2/10), math.log(51/50)))), rel=1e-3
    )
    assert match.subject_tdi == 40.0
    assert match.subject_cr == 10.0
    assert match.subject_isf == 50.0
    assert match.user_tdi == 41.0
    assert match.user_cr == 10.2
    assert match.user_isf == 51.0


def test_match_subject_marks_low_confidence():
    subjects = {"a": FakeSubject("a", 10.0, 40.0, 50.0)}
    assert match_subject(subjects, tdi=120.0, cr=30.0, isf=150.0).confidence == "low"


def test_trace_scenario_uses_real_meals_and_warmup():
    from nightscout.trace import build_trace
    T0 = 1_790_000_000_000
    treatments = [
        {"eventType": "Carb Correction", "date": T0 - 60 * 60_000, "carbs": 30},
        {"eventType": "Meal Bolus", "date": T0 - 30 * 60_000, "insulin": 3.0, "type": "NORMAL"},
        {"eventType": "Carb Correction", "date": T0 + 60 * 60_000, "carbs": 40},
        {"eventType": "Correction Bolus", "date": T0 + 60 * 60_000, "insulin": 1.0},
        {"eventType": "Correction Bolus", "date": T0 + 90 * 60_000, "insulin": 2.0, "type": "SMB"},
    ]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 120}],
                        treatments=treatments,
                        profiles=[{"startDate": T0 - 86_400_000, "defaultProfile": "Main",
                                   "store": {"Main": {"units": "mmol", "timezone": "UTC",
                                                      "sens": [{"time": "00:00", "value": 5.0}],
                                                      "carbratio": [{"time": "00:00", "value": 10.0}],
                                                      "basal": [{"time": "00:00", "value": 1.0}],
                                                      "target_low": [{"time": "00:00", "value": 5.5}],
                                                      "target_high": [{"time": "00:00", "value": 7.0}]}}}],
                        devicestatus=[], window_start=T0, window_end=T0 + 2 * 3600_000)
    scenario = trace_scenario(trace, warmup_hours=8.0)
    assert scenario.duration_min == 8 * 60 + 120
    assert scenario.meals[0] == (7 * 60, 30.0)
    assert scenario.meals[1] == (9 * 60, 40.0)
    assert scenario.boluses == [(7 * 60 + 30, 3.0), (9 * 60, 1.0)]
    assert scenario.meal_mode == "announced"


def test_trace_scenario_includes_pre_window_loop_boluses_only():
    from nightscout.trace import build_trace
    T0 = 1_790_000_000_000
    treatments = [
        {"eventType": "Correction Bolus", "date": T0 - 60 * 60_000, "insulin": 3.0, "type": "NORMAL"},
        {"eventType": "Correction Bolus", "date": T0 - 30 * 60_000, "insulin": 1.0, "type": "SMB"},
        {"eventType": "Correction Bolus", "date": T0 + 30 * 60_000, "insulin": 2.0, "type": "SMB"},
    ]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 120}],
                        treatments=treatments,
                        profiles=[{"startDate": T0 - 86_400_000, "defaultProfile": "Main",
                                   "store": {"Main": {"units": "mg/dL", "timezone": "UTC",
                                                      "sens": [{"time": "00:00", "value": 50.0}],
                                                      "carbratio": [{"time": "00:00", "value": 10.0}],
                                                      "basal": [{"time": "00:00", "value": 1.0}],
                                                      "target_low": [{"time": "00:00", "value": 100.0}],
                                                      "target_high": [{"time": "00:00", "value": 120.0}]}}}],
                        devicestatus=[], window_start=T0, window_end=T0 + 2 * 3600_000)
    scenario = trace_scenario(trace, warmup_hours=8.0)
    assert scenario.boluses == [(7 * 60, 3.0), (7 * 60 + 30, 1.0)]


class FakeBridge:
    def __init__(self):
        self.init = None
        self.cycles = []

    def request(self, payload):
        if payload["cmd"] == "init":
            self.init = payload
            return {}
        self.cycles.append(payload)
        return {
            "glucose": {"glucose": 120.0},
            "iob": {"iob": [{"iob": 0.0}]},
            "rt": {"insulinReq": 0.0, "reason": "", "rate": 1.0, "duration": 0, "units": 0.0},
        }


def test_init_bridge_seeds_boluses_from_before_the_scenario():
    from runner.closed_loop import EPOCH0_MS, AlgorithmProfile, Scenario, init_bridge
    from simcore.params import load_subject

    bridge = FakeBridge()
    scenario = Scenario(boluses=[(-5.0, 2.0), (10.0, 3.0)])
    init_bridge(bridge, load_subject("adult#001"), {}, {}, AlgorithmProfile(
        sens=50.0, carb_ratio=10.0, target_bg=110.0,
    ), scenario)
    boluses = [row for row in bridge.init["treatments"] if row["kind"] == "bolus"]
    assert boluses == [{
        "kind": "bolus", "ts": EPOCH0_MS - 5 * 60_000,
        "amount": 2.0, "type": "NORMAL", "isValid": True,
    }]


def test_closed_loop_delivers_scenario_boluses_to_model_and_bridge():
    from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
    from simcore.params import load_subject

    subject = load_subject("adult#001")
    profile = AlgorithmProfile(
        sens=50.0, carb_ratio=10.0, target_bg=110.0,
        current_basal=subject.basal_u_per_min * 60,
    )
    scenario = Scenario(duration_min=10, boluses=[(2.0, 1.5)])
    bridge = FakeBridge()
    init_bridge(bridge, subject, {}, {}, profile, scenario)
    loop = run_closed_loop(
        subject, bridge, profile, scenario, temp_mode="cycle", noise_kind="none",
    )
    assert loop.bolus_units_to_model == pytest.approx(1.5)
    assert [kind for _, _, kind in loop.dose_events].count("manual") == 1
    seen = [
        row for row in bridge.cycles[0]["treatments"]
        if row["kind"] == "bolus"
    ]
    assert seen and seen[0]["amount"] == pytest.approx(1.5)
