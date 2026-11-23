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


def test_seed_history_excludes_smb_and_keeps_short_temp_duration():
    trace = make_trace()
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 - 60 * MIN, kind="bolus", amount=0.9, duration_min=0.0, origin="loop"))
    trace.treatments.append(type(trace.treatments[0])(
        ts=T0 - 30 * MIN, kind="temp_basal", amount=1.4, duration_min=6.0, origin="pump"))
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    seeded = bridge.requests[0]["treatments"]
    assert all(not (t["kind"] == "bolus" and t["amount"] == 0.9) for t in seeded)
    temp = next(t for t in seeded if t["kind"] == "temp")
    assert temp["rate"] == 1.4
    assert temp["duration"] == 6 * 60_000
    first_cycle = next(r for r in bridge.requests if r["cmd"] == "cycle")
    assert any(t["kind"] == "temp" and t["rate"] == 1.4 for t in first_cycle["treatments"])


def test_replay_skips_cgm_gap_and_reports_it():
    trace = make_trace()
    trace.entries = [e for e in trace.entries if e.ts < T0 + 10 * MIN]
    bridge = FakeBridge()
    result = run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A"), bridge)
    assert result.skipped
    assert any("gap" in w for w in result.warnings)


def test_smb_off_disables_micro_bolus():
    trace = make_trace()
    bridge = FakeBridge()
    run_replay(trace, {}, {"label": "x"}, ReplayVariant(label="A", smb=False), bridge)
    cycle = next(r for r in bridge.requests if r["cmd"] == "cycle")
    assert cycle["input"]["microBolusAllowed"] is False
