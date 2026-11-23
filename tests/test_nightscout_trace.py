import sys
from pathlib import Path

import pytest

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
    assert trace.profile_at(T0).carb_ratio[0][1] == 10.0
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


def test_mmol_profile_scales_only_bg_unit_fields():
    doc = profile_doc(T0, 5.0, 12.0, 1.0)
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}], treatments=[],
                        profiles=[doc], devicestatus=[], window_start=T0, window_end=T0 + 1)
    segment = trace.profile_at(T0)
    assert segment.carb_ratio_at(T0) == 12.0
    assert segment.sens_at(T0) == 90.0
    assert segment.target_low_at(T0) == 99.0


def test_temp_target_respects_record_units():
    treatments = [{"eventType": "Temporary Target", "date": T0, "targetBottom": 100,
                   "targetTop": 120, "duration": 30, "units": "mg/dL"}]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}],
                        treatments=treatments, profiles=[profile_doc(T0, 50.0, 10.0, 1.0)],
                        devicestatus=[], window_start=T0, window_end=T0 + 1)
    assert trace.active_temp_target(T0 + MIN) == (100.0, 120.0)


def test_temp_target_unknown_units_are_not_guessed():
    treatments = [{"eventType": "Temporary Target", "date": T0, "targetBottom": 5.0,
                   "targetTop": 6.0, "duration": 30, "units": "banana"}]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}],
                        treatments=treatments, profiles=[profile_doc(T0, 50.0, 10.0, 1.0)],
                        devicestatus=[], window_start=T0, window_end=T0 + 1)
    assert trace.active_temp_target(T0 + MIN) == (5.0, 6.0)
    assert any("temp target" in w for w in trace.warnings)

def test_profile_switch_applies_profile_json_and_percentage():
    import json

    doc = profile_doc(T0, 5.0, 10.0, 1.0)
    switch_ts = T0 + 60 * MIN
    treatments = [{
        "eventType": "Profile Switch", "date": switch_ts, "profile": "Evening",
        "percentage": 90, "profileJson": json.dumps(doc),
    }]
    trace = build_trace(
        entries=[{"type": "sgv", "date": T0, "sgv": 100}],
        treatments=treatments,
        profiles=[profile_doc(T0 - 86_400_000, 5.0, 10.0, 1.0)],
        devicestatus=[], window_start=T0, window_end=T0 + 3 * 3600_000,
    )
    before = trace.profile_at(T0)
    after = trace.profile_at(switch_ts + MIN)
    assert after.start_ms == switch_ts
    assert before.basal[0][1] == 1.0
    assert after.basal[0][1] == pytest.approx(0.9)
    assert after.sens[0][1] == pytest.approx(90.0 * 100 / 90)
    assert after.carb_ratio[0][1] == pytest.approx(10.0 * 100 / 90)
    assert after.target_low[0][1] == pytest.approx(99.0)
    assert any("profile switches applied: 1" in w for w in trace.warnings)


def test_profile_switch_without_usable_json_is_reported():
    treatments = [{
        "eventType": "Profile Switch", "date": T0 + 30 * MIN,
        "profile": "Evening", "percentage": 80, "profileJson": "not json",
    }]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}],
                        treatments=treatments,
                        profiles=[profile_doc(T0 - 86_400_000, 5.0, 10.0, 1.0)],
                        devicestatus=[], window_start=T0, window_end=T0 + 2 * 3600_000)
    assert all(segment.start_ms < T0 + 30 * MIN for segment in trace.profiles)
    assert any("without usable profileJson" in w for w in trace.warnings)


def test_exercise_and_other_events_are_reported():
    treatments = [
        {"eventType": "Exercise", "date": T0, "duration": 30},
        {"eventType": "Site Change", "date": T0 + MIN},
    ]
    trace = build_trace(entries=[{"type": "sgv", "date": T0, "sgv": 100}],
                        treatments=treatments,
                        profiles=[profile_doc(T0 - 86_400_000, 5.0, 10.0, 1.0)],
                        devicestatus=[], window_start=T0, window_end=T0 + 3600_000)
    assert any("exercise events not reproduced: 1" in w for w in trace.warnings)
    assert any("other Nightscout events not replayed: 1" in w for w in trace.warnings)


def test_history_start_keeps_pre_window_cgm_points():
    entries = [
        {"type": "sgv", "date": T0 - 60 * MIN, "sgv": 90},
        {"type": "sgv", "date": T0 + MIN, "sgv": 120},
    ]
    profiles = [profile_doc(T0 - 86_400_000, 50.0, 10.0, 1.0)]
    default = build_trace(entries=entries, treatments=[], profiles=profiles,
                          devicestatus=[], window_start=T0, window_end=T0 + 3600_000)
    assert [point.mgdl for point in default.entries] == [120]
    assert default.window_start == T0
    extended = build_trace(entries=entries, treatments=[], profiles=profiles,
                           devicestatus=[], window_start=T0, window_end=T0 + 3600_000,
                           history_start=T0 - 8 * 3_600_000)
    assert [point.mgdl for point in extended.entries] == [90, 120]
    assert extended.window_start == T0
