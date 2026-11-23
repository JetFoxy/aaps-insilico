import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from nightscout.trace import build_trace

T0 = 1_790_000_000_000
MIN = 60_000


def profile_doc(start, sens, cr, basal, low=5.5, high=7.0):
    return {
        "startDate": start, "defaultProfile": "Main",
        "store": {"Main": {
            "units": "mmol", "timezone": "Europe/Moscow",
            "sens": [{"time": "00:00", "value": sens}],
            "carbratio": [{"time": "00:00", "value": cr}],
            "basal": [{"time": "00:00", "value": basal}],
            "target_low": [{"time": "00:00", "value": low}],
            "target_high": [{"time": "00:00", "value": high}],
        }},
    }


def test_build_trace_normalizes_units_and_events():
    entries = [
        {"type": "sgv", "date": T0 + 1 * MIN, "sgv": 120, "direction": "Flat"},
        {"type": "sgv", "date": T0 + 2 * MIN, "sgv": 121, "direction": "Flat"},
        {"type": "cal", "date": T0 + 3 * MIN, "sgv": 0},
    ]
    treatments = [
        {"eventType": "Correction Bolus", "created_at": "2026-09-01T00:00:00Z",
         "date": T0, "insulin": 0.6, "type": "SMB"},
        {"eventType": "Correction Bolus", "created_at": "2026-09-01T00:05:00Z",
         "date": T0 + 5 * MIN, "insulin": 2.0, "type": "NORMAL"},
        {"eventType": "Temp Basal", "created_at": "2026-09-01T00:05:00Z",
         "date": T0 + 5 * MIN, "absolute": 1.2, "duration": 6},
        {"eventType": "Temporary Target", "created_at": "2026-09-01T00:05:00Z",
         "date": T0 + 5 * MIN, "targetBottom": 5.0, "targetTop": 6.0, "duration": 20},
    ]
    devicestatus = [{"created_at": "2026-09-01T00:05:00Z", "openaps": {"suggested": {
        "deliverAt": "2026-09-01T00:05:00Z", "insulinReq": 0.4,
        "sensitivityRatio": 0.95, "rate": 1.1,
        "autoIsf": {"acceIsf": 1.1, "bgIsf": 0.9, "ppIsf": 1.0, "duraIsf": 1.0,
                    "finalIsf": 0.99, "smbOn": True},
    }}}]
    trace = build_trace(entries=entries, treatments=treatments,
                        profiles=[profile_doc(T0 - 86_400_000, 50.0, 10.0, 1.0)],
                        devicestatus=devicestatus, window_start=T0, window_end=T0 + 3600_000)
    assert [p.mgdl for p in trace.entries] == [120, 121]
    assert trace.profile_at(T0).sens[0][1] == 900.0
    assert trace.profile_at(T0).carb_ratio[0][1] == 180.0
    assert trace.treatments[0].origin == "loop"
    assert trace.treatments[1].origin == "manual"
    assert trace.active_temp_target(T0 + 10 * MIN) == (5.0 * 18, 6.0 * 18)
    assert trace.active_temp_target(T0 + 30 * MIN) is None
    assert trace.autosens_at(T0 + 10 * MIN) == 0.95
    assert trace.temp_basal_at(T0 + 7 * MIN) == (1.2, 4.0)
    assert trace.decisions[0].factors["acceIsf"] == 1.1
    assert trace.decisions[0].smb_on is True


def test_profile_values_follow_time_of_day():
    doc = profile_doc(T0 - 86_400_000, 50.0, 10.0, 1.0)
    doc["store"]["Main"]["basal"] = [{"time": "00:00", "value": 0.8}, {"time": "06:00", "value": 1.4}]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}], treatments=[],
                        profiles=[doc], devicestatus=[], window_start=T0, window_end=T0 + 1)
    segment = trace.profile_at(T0)
    assert segment.basal_at(T0) in (0.8, 1.4)
    assert segment.sens_at(T0) == 900.0


def test_untyped_bolus_is_reported_not_guessed():
    treatments = [{"eventType": "Correction Bolus", "date": T0, "insulin": 1.0}]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}],
                        treatments=treatments, profiles=[profile_doc(T0, 50.0, 10.0, 1.0)],
                        devicestatus=[], window_start=T0, window_end=T0 + 1)
    assert trace.treatments[0].origin == "unknown"
    assert any("unclassified" in w for w in trace.warnings)
