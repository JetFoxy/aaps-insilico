import io
import json
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nightscout.cli import main
from nightscout_stub import StubNightscout

NOW = int(time.time() * 1000)
PROFILE = {"startDate": NOW - 86_400_000, "defaultProfile": "Main", "store": {"Main": {
    "units": "mmol", "timezone": "Europe/Moscow",
    "sens": [{"time": "00:00", "value": 5.0}],
    "carbratio": [{"time": "00:00", "value": 10.0}],
    "basal": [{"time": "00:00", "value": 1.0}],
    "target_low": [{"time": "00:00", "value": 5.5}],
    "target_high": [{"time": "00:00", "value": 7.0}],
}}}


def test_import_summary_has_no_token():
    routes = {
        "/api/v1/status.json": lambda q: (200, {"apiEnabled": True}),
        "/api/v1/profile.json": lambda q: (200, [PROFILE]),
        "/api/v1/entries.json": lambda q: (200, [{"type": "sgv", "date": NOW - 60_000, "sgv": 110}]),
        "/api/v1/treatments.json": lambda q: (200, []),
        "/api/v1/devicestatus.json": lambda q: (200, []),
    }
    stub = StubNightscout(routes)
    out = io.StringIO()
    with stub, redirect_stdout(out):
        code = main(["import", "--url", stub.url, "--token", "tok-secret", "--days", "1"])
    text = out.getvalue()
    assert code == 0
    assert "tok-secret" not in text
    summary = json.loads(text)
    assert summary["entries"] == 1
    assert summary["prefs_loaded"] is False
    assert any("devicestatus" in w for w in summary["warnings"])


def test_import_caps_window_at_fourteen_days():
    routes = {
        "/api/v1/status.json": lambda q: (200, {"apiEnabled": True}),
        "/api/v1/profile.json": lambda q: (200, [PROFILE]),
        "/api/v1/entries.json": lambda q: (200, []),
        "/api/v1/treatments.json": lambda q: (200, []),
        "/api/v1/devicestatus.json": lambda q: (200, []),
    }
    stub = StubNightscout(routes)
    out = io.StringIO()
    with stub, redirect_stdout(out):
        code = main(["import", "--url", stub.url, "--days", "30"])
    summary = json.loads(out.getvalue())
    assert code == 0
    assert summary["window_end"] - summary["window_start"] == 14 * 86_400_000
    assert any("cap" in w and "14" in w for w in summary["warnings"])
