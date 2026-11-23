import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from nightscout.trace import build_trace
from runner.replay_loop import ReplayVariant, run_replay

T0 = 1_790_000_000_000
MIN = 60_000


class FakeBridge:
    def __init__(self):
        self.requests = []
        self.n = 0

    def request(self, payload):
        self.requests.append(payload)
        if payload["cmd"] == "init":
            return {"ok": True}
        self.n += 1
        return {
            "rt": {"rate": 1.1, "units": 0.2, "insulinReq": 0.4, "eventualBG": 130.0,
                   "sensitivityRatio": 0.95, "reason": "test"},
            "algorithm": {"sens": 52.0, "aiv": {
                "acceIsf": 1.1, "bgIsf": 0.9, "ppIsf": 1.0, "duraIsf": 1.0,
                "finalIsf": 52.0, "iobThEffective": 5.0}},
            "iob": {"iob": [{"iob": 1.5}]},
        }


def make_trace():
    entries = [{"type": "sgv", "date": T0 + i * MIN, "sgv": 120 + i} for i in range(0, 40, 2)]
    treatments = [{"eventType": "Temporary Target", "date": T0, "targetBottom": 5.0,
                   "targetTop": 6.0, "duration": 20}]
    profiles = [{"startDate": T0 - 86_400_000, "defaultProfile": "Main", "store": {"Main": {
        "units": "mmol", "timezone": "Europe/Moscow",
        "sens": [{"time": "00:00", "value": 5.0}],
        "carbratio": [{"time": "00:00", "value": 10.0}],
        "basal": [{"time": "00:00", "value": 1.0}],
        "target_low": [{"time": "00:00", "value": 5.5}],
        "target_high": [{"time": "00:00", "value": 7.0}],
    }}}]
    return build_trace(entries=entries, treatments=treatments, profiles=profiles,
                       devicestatus=[], window_start=T0, window_end=T0 + 30 * MIN)


def test_replay_passes_targets_autosens_and_keeps_own_smb():
    trace = make_trace()
    bridge = FakeBridge()
    variant = ReplayVariant(label="A", prefs={"openapsama_smb_delivery_ratio": "0.7"})
    result = run_replay(trace, {"openapsama_smb_delivery_ratio": "0.3"}, {"label": "x"}, variant, bridge)
    init = bridge.requests[0]
    assert init["cmd"] == "init"
    cycles = [r for r in bridge.requests if r["cmd"] == "cycle"]
    assert len(cycles) == 7
    assert cycles[0]["input"]["profile"]["temptargetSet"] is True
    assert cycles[0]["input"]["autosens"]["ratio"] == 1.0
    assert cycles[0]["input"]["smbRatio"] == 0.7
    assert cycles[0]["input"]["microBolusAllowed"] is True
    assert result.cycles[0].smb == 0.2
    assert any(t["kind"] == "bolus" and t["amount"] == 0.2 and t["type"] == "SMB"
               for t in cycles[1]["treatments"])


def test_init_basal_follows_the_profile_schedule():
    trace = make_trace()
    trace.profiles[0] = type(trace.profiles[0])(
        start_ms=trace.profiles[0].start_ms,
        basal=[(0, 0.8), (360, 1.4)],
        sens=trace.profiles[0].sens,
        carb_ratio=trace.profiles[0].carb_ratio,
        target_low=trace.profiles[0].target_low,
        target_high=trace.profiles[0].target_high,
        units=trace.profiles[0].units,
        timezone=trace.profiles[0].timezone,
    )
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    blocks = bridge.requests[0]["basal"]
    assert len(blocks) >= 2
    assert any(rate == 1.4 for _, rate in blocks)


def test_seed_history_includes_pre_window_smb_and_keeps_short_temp_duration():
    trace = make_trace()
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 - 60 * MIN, kind="bolus", amount=0.9, duration_min=0.0, origin="loop"))
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 - 30 * MIN, kind="temp_basal", amount=1.4, duration_min=6.0, origin="pump"))
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    seeded = bridge.requests[0]["treatments"]
    smb = next(t for t in seeded if t["kind"] == "bolus")
    assert smb["amount"] == 0.9
    assert smb["type"] == "SMB"
    temp = next(t for t in seeded if t["kind"] == "temp")
    assert temp["rate"] == 1.4
    assert temp["duration"] == 6 * 60_000
    first_cycle = next(r for r in bridge.requests if r["cmd"] == "cycle")
    assert any(t["kind"] == "temp" and t["rate"] == 1.4 for t in first_cycle["treatments"])


def test_seed_history_excludes_in_window_loop_boluses():
    trace = make_trace()
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 5 * MIN, kind="bolus", amount=0.7, duration_min=0.0, origin="loop"))
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    seeded = bridge.requests[0]["treatments"]
    assert all(not (t["kind"] == "bolus" and t["amount"] == 0.7) for t in seeded)


def test_replay_skips_cgm_gap_and_reports_it():
    trace = make_trace()
    trace.entries = [e for e in trace.entries if e.ts < T0 + 10 * MIN]
    bridge = FakeBridge()
    result = run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    assert result.skipped
    assert any("gap" in w for w in result.warnings)


def test_in_window_real_boluses_and_carbs_reach_the_bridge():
    trace = make_trace()
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 10 * MIN, kind="bolus", amount=3.0, duration_min=0.0, origin="manual"))
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 10 * MIN, kind="carbs", amount=40.0, duration_min=0.0, origin="manual"))
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 10 * MIN, kind="bolus", amount=0.7, duration_min=0.0, origin="loop"))
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    early = [r for r in bridge.requests if r["cmd"] == "cycle"][0]
    later = [r for r in bridge.requests if r["cmd"] == "cycle"][3]
    assert all(not (t["kind"] == "bolus" and t["amount"] == 3.0) for t in early["treatments"])
    assert any(t["kind"] == "bolus" and t["amount"] == 3.0 for t in later["treatments"])
    assert any(t["kind"] == "carbs" and t["amount"] == 40.0 for t in later["treatments"])
    assert all(not (t["kind"] == "bolus" and t["amount"] == 0.7) for t in later["treatments"])
    assert later["mealMode"] == "announced"


def test_in_window_untyped_bolus_is_included_with_warning():
    trace = make_trace()
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 + 5 * MIN, kind="bolus", amount=1.5, duration_min=0.0, origin="unknown"))
    bridge = FakeBridge()
    result = run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    last = [r for r in bridge.requests if r["cmd"] == "cycle"][-1]
    assert any(t["kind"] == "bolus" and t["amount"] == 1.5 for t in last["treatments"])
    assert any("unclassified" in w for w in result.warnings)


def test_replay_uses_recorded_decision_times():
    trace = make_trace()
    from nightscout.trace import DecisionRecord
    trace.decisions = [
        DecisionRecord(ts=T0 + 2 * MIN, deliver_at=T0 + 2 * MIN, insulin_req=0.1,
                       final_isf=1.0, factors={}, smb_on=False, rate=None,
                       eventual_bg=None, sensitivity_ratio=None),
        DecisionRecord(ts=T0 + 5 * MIN, deliver_at=T0 + 5 * MIN, insulin_req=0.1,
                       final_isf=1.0, factors={}, smb_on=False, rate=None,
                       eventual_bg=None, sensitivity_ratio=None),
        DecisionRecord(ts=T0 + 9 * MIN, deliver_at=T0 + 9 * MIN, insulin_req=0.1,
                       final_isf=1.0, factors={}, smb_on=False, rate=None,
                       eventual_bg=None, sensitivity_ratio=None),
    ]
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    times = [r["now"] for r in bridge.requests if r["cmd"] == "cycle"]
    assert times == [T0 + 2 * MIN, T0 + 5 * MIN, T0 + 9 * MIN]


def test_replay_uses_the_profile_active_after_a_switch():
    import json

    entries = [{"type": "sgv", "date": T0 + i * MIN, "sgv": 120} for i in range(0, 41, 2)]
    baseline = {"startDate": T0 - 86_400_000, "defaultProfile": "Main", "store": {"Main": {
        "units": "mmol", "timezone": "UTC",
        "sens": [{"time": "00:00", "value": 5.0}],
        "carbratio": [{"time": "00:00", "value": 10.0}],
        "basal": [{"time": "00:00", "value": 1.0}],
        "target_low": [{"time": "00:00", "value": 5.5}],
        "target_high": [{"time": "00:00", "value": 7.0}],
    }}}
    evening = {"startDate": T0, "defaultProfile": "Evening", "store": {"Evening": {
        "units": "mmol", "timezone": "UTC",
        "sens": [{"time": "00:00", "value": 10.0}],
        "carbratio": [{"time": "00:00", "value": 10.0}],
        "basal": [{"time": "00:00", "value": 1.0}],
        "target_low": [{"time": "00:00", "value": 5.5}],
        "target_high": [{"time": "00:00", "value": 7.0}],
    }}}
    treatments = [{"eventType": "Profile Switch", "date": T0 + 10 * MIN,
                   "profile": "Evening", "profileJson": json.dumps(evening)}]
    trace = build_trace(entries=entries, treatments=treatments, profiles=[baseline],
                        devicestatus=[], window_start=T0, window_end=T0 + 30 * MIN)
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    cycles = [r for r in bridge.requests if r["cmd"] == "cycle"]
    before = next(r for r in cycles if r["now"] == T0)
    after = next(r for r in cycles if r["now"] == T0 + 10 * MIN)
    assert before["isf"] == 90.0
    assert after["isf"] == 180.0


def test_replay_reads_iob_threshold_and_button_increment_from_settings():
    trace = make_trace()
    bridge = FakeBridge()
    prefs = {"iob_threshold_percent": "85", "insulin_button_increment_1": "0.5"}
    run_replay(trace, prefs, {"label": "x"}, ReplayVariant(label="A"), bridge)
    cycle = next(r for r in bridge.requests if r["cmd"] == "cycle")
    assert cycle["input"]["iobThresholdPercent"] == 85
    assert cycle["input"]["profile"]["bolus_increment"] == 0.5


def test_replay_reports_the_bolus_increment_default():
    trace = make_trace()
    bridge = FakeBridge()
    result = run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    assert any("bolus_increment" in warning for warning in result.warnings)


def test_smb_off_disables_micro_bolus():
    trace = make_trace()
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A", smb=False), bridge)
    cycle = next(r for r in bridge.requests if r["cmd"] == "cycle")
    assert cycle["input"]["microBolusAllowed"] is False
