import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from golden.check_replay_parity import DEFAULT_TOLERANCES, compare
from nightscout.trace import DecisionRecord
from runner.replay_loop import ReplayCycle, ReplayResult, ReplayVariant

T0 = 1_790_000_000_000


def decision(ts, isf=50.0, req=0.4, rate=1.1, smb=True, acce=1.0):
    return DecisionRecord(ts=ts, deliver_at=ts, insulin_req=req, final_isf=isf,
                          factors={"acceIsf": acce}, smb_on=smb, rate=rate,
                          eventual_bg=120.0, sensitivity_ratio=1.0)


def cycle(ts, isf=50.0, req=0.4, rate=1.1, smb=0.2, acce=1.0):
    return ReplayCycle(ts=ts, glucose=120.0, rate=rate, smb=smb, insulin_req=req,
                       final_isf=isf, factors={"acceIsf": acce}, iob=1.0,
                       eventual_bg=120.0, sensitivity_ratio=1.0, reason="")


def test_trace_json_round_trip():
    from nightscout.trace import build_trace, trace_from_dict, trace_to_dict
    T0 = 1_790_000_000_000
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}], treatments=[],
                        profiles=[], devicestatus=[], window_start=T0, window_end=T0 + 1)
    again = trace_from_dict(trace_to_dict(trace))
    assert again.entries == trace.entries
    assert again.window_start == T0


def test_trace_round_trip_keeps_every_nested_record():
    from nightscout.trace import build_trace, trace_from_dict, trace_to_dict
    profiles = [{"startDate": T0 - 86_400_000, "defaultProfile": "Main", "store": {"Main": {
        "units": "mmol", "timezone": "UTC",
        "sens": [{"time": "00:00", "value": 5.0}],
        "carbratio": [{"time": "00:00", "value": 10.0}],
        "basal": [{"time": "00:00", "value": 1.0}],
        "target_low": [{"time": "00:00", "value": 5.5}],
        "target_high": [{"time": "00:00", "value": 7.0}],
    }}}]
    treatments = [{"eventType": "Temp Basal", "date": T0, "absolute": 1.2, "duration": 30}]
    devicestatus = [{"created_at": T0, "openaps": {"suggested": {
        "deliverAt": T0, "insulinReq": 0.4, "rate": 1.0,
        "autoIsf": {"finalIsf": 1.05, "smbOn": True, "acceIsf": 1.0},
    }}}]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}],
                        treatments=treatments, profiles=profiles, devicestatus=devicestatus,
                        window_start=T0, window_end=T0 + 1)
    again = trace_from_dict(trace_to_dict(trace))
    assert again == trace
    assert again.profiles[0].sens == [(0, 90.0)]
    assert again.decisions[0].factors == {"acceIsf": 1.0}


def test_exact_match_is_full():
    replay = ReplayResult(ReplayVariant(label="A"), [cycle(T0), cycle(T0 + 300_000)], [], [])
    stats = compare(replay, [decision(T0), decision(T0 + 300_000)])
    assert stats["final_isf"]["match"] == 1.0
    assert stats["rate"]["max_error"] == 0.0
    assert stats["smb_on"]["match"] == 1.0


def test_tolerance_is_fixed_and_reported():
    replay = ReplayResult(ReplayVariant(label="A"), [cycle(T0, isf=50.02)], [], [])
    stats = compare(replay, [decision(T0)])
    assert stats["final_isf"]["match"] == 0.0
    assert stats["final_isf"]["max_error"] == 0.02
    assert DEFAULT_TOLERANCES["final_isf"] == 0.01


def test_unmatched_decision_is_counted_and_factors_compared():
    replay = ReplayResult(ReplayVariant(label="A"), [cycle(T0, acce=1.05)], [], [])
    stats = compare(replay, [decision(T0), decision(T0 + 600_000)])
    assert stats["unmatched"]["n"] == 1
    assert stats["factor:acceIsf"]["match"] == 0.0
    assert stats["factor:acceIsf"]["max_error"] == 0.05


def test_smb_on_uses_the_delivered_amount():
    replay = ReplayResult(
        ReplayVariant(label="A"), [cycle(T0, smb=0.0), cycle(T0 + 300_000, smb=0.4)], [], []
    )
    stats = compare(replay, [decision(T0, smb=False), decision(T0 + 300_000, smb=True)])
    assert stats["smb_on"]["match"] == 1.0


def test_final_isf_prefers_the_autoisf_factor_over_absolute_sensitivity():
    replay = ReplayResult(ReplayVariant(label="A"), [cycle(T0, isf=45.0)], [], [])
    replay.cycles[0].factors["finalIsf"] = 1.05
    stats = compare(replay, [decision(T0, isf=1.05)])
    assert stats["final_isf"]["match"] == 1.0
