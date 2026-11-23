import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import server.live as live
from nightscout.trace import build_trace
from nightscout_stub import StubNightscout
from runner.replay_loop import ReplayCycle, ReplayResult

T0 = 1_790_000_000_000
MIN = 60_000


def start():
    server = ThreadingHTTPServer(("127.0.0.1", 0), live.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_import_returns_summary_without_token(monkeypatch):
    monkeypatch.setattr(live, "whatif_fetch", lambda payload: ({"entries": 3}, "trace-1"))
    server = start()
    try:
        body = json.dumps({"url": "http://ns", "token": "tok-secret", "days": 3}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/whatif/import",
                                     data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as response:
            payload = json.loads(response.read())
        assert payload == {"trace_id": "trace-1", "summary": {"entries": 3}}
        assert "tok-secret" not in json.dumps(payload)
    finally:
        server.shutdown()


def test_unknown_trace_is_404():
    server = start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/whatif/trace/nope")
        assert exc.value.code == 404
    finally:
        server.shutdown()


def fetch_json(server, path, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def read_stream(server, run_id):
    frames = []
    url = f"http://127.0.0.1:{server.server_port}/api/whatif/stream?id={run_id}"
    with urllib.request.urlopen(url) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line.startswith("data: "):
                frames.append(json.loads(line[6:]))
    return frames


def make_trace():
    entries = [{"type": "sgv", "date": T0 + i * MIN, "sgv": 120 + i} for i in range(0, 40, 2)]
    profiles = [{
        "startDate": T0 - 86_400_000,
        "defaultProfile": "Main",
        "store": {"Main": {
            "units": "mmol",
            "timezone": "UTC",
            "sens": [{"time": "00:00", "value": 5.0}],
            "carbratio": [{"time": "00:00", "value": 10.0}],
            "basal": [{"time": "00:00", "value": 1.0}],
            "target_low": [{"time": "00:00", "value": 5.5}],
            "target_high": [{"time": "00:00", "value": 7.0}],
        }},
    }]
    return build_trace(
        entries=entries, treatments=[], profiles=profiles, devicestatus=[],
        window_start=T0, window_end=T0 + 30 * MIN,
    )


def register(trace_id="t1", prefs_loaded=True):
    live.TRACES[trace_id] = make_trace()
    live.WHATIF_SETTINGS[trace_id] = {
        "prefs": {"openapsmb_max_iob": "6"},
        "insulin": {"label": "test"},
        "prefs_loaded": prefs_loaded,
        "created": time.time(),
    }
    return trace_id


class FakeBridge:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_cycles(variant):
    return ReplayResult(variant=variant, cycles=[
        ReplayCycle(
            ts=T0, glucose=120.0, rate=1.1, smb=0.2, insulin_req=0.4, final_isf=52.0,
            factors={"bgIsf": 0.9}, iob=1.5, eventual_bg=130.0,
            sensitivity_ratio=0.95, reason="test",
        ),
    ], skipped=[], warnings=["excluded loop boluses: 1"])


def test_import_builds_trace_and_hides_network_secrets():
    now = int(time.time() * 1000)
    profile = {
        "startDate": now - 86_400_000,
        "defaultProfile": "Main",
        "store": {"Main": {
            "units": "mg/dL", "timezone": "UTC",
            "sens": [{"time": "00:00", "value": 50}],
            "carbratio": [{"time": "00:00", "value": 10}],
            "basal": [{"time": "00:00", "value": 1.0}],
            "target_low": [{"time": "00:00", "value": 100}],
            "target_high": [{"time": "00:00", "value": 130}],
        }},
    }
    entries = [
        {"type": "sgv", "date": now - 60 * MIN, "sgv": 120},
        {"type": "sgv", "date": now - 30 * MIN, "sgv": 130},
        {"type": "sgv", "date": now - 10 * MIN, "sgv": 140},
    ]
    routes = {
        "/api/v1/status.json": lambda query: (200, {}),
        "/api/v1/profile.json": lambda query: (200, [profile]),
        "/api/v1/entries.json": lambda query: (200, entries),
        "/api/v1/treatments.json": lambda query: (200, []),
        "/api/v1/devicestatus.json": lambda query: (200, []),
    }
    stub = StubNightscout(routes)
    with stub:
        summary, trace_id = live.whatif_fetch(
            {"url": stub.url, "token": "tok-secret", "days": 1}
        )
        try:
            assert summary["entries"] == 3
            assert summary["profiles"] == 1
            assert summary["prefs_loaded"] is False
            assert trace_id in live.TRACES
            text = json.dumps(summary)
            assert "tok-secret" not in text
            assert stub.url not in text
        finally:
            live.TRACES.pop(trace_id, None)
            live.WHATIF_SETTINGS.pop(trace_id, None)


def test_fetch_errors_never_echo_url_or_token(monkeypatch):
    class Broken:
        def __init__(self, url, token=None):
            self.url = url

        def status(self):
            raise RuntimeError(f"boom tok-secret {self.url}")

    monkeypatch.setattr(live, "NightscoutClient", Broken)
    with pytest.raises(live.WhatifError) as exc:
        live.whatif_fetch({"url": "https://nightscout.example", "token": "tok-secret", "days": 1})
    text = str(exc.value)
    assert exc.value.status == 500
    assert "tok-secret" not in text
    assert "nightscout.example" not in text


def test_options_expose_modes_algorithms_and_prefs():
    server = start()
    try:
        payload = fetch_json(server, "/api/whatif/options")
    finally:
        server.shutdown()
    assert set(payload["modes"]) == {"replay", "model"}
    assert set(payload["algorithms"]) == {"off", "autoisf", "dynisf"}
    assert set(payload["prefs"]) == set(live.UI_PREF_KEYS)
    assert payload["defaults"]["days"] == live.WHATIF_DEFAULT_DAYS


def test_trace_route_returns_the_import_summary():
    trace_id = register("summary-1")
    server = start()
    try:
        summary = fetch_json(server, f"/api/whatif/trace/{trace_id}")
    finally:
        server.shutdown()
        live.TRACES.pop(trace_id, None)
        live.WHATIF_SETTINGS.pop(trace_id, None)
    assert summary["entries"] == 16
    assert summary["window_start"] == T0
    assert summary["prefs_loaded"] is True


def test_replay_streams_start_variant_cycle_done(monkeypatch):
    trace_id = register("run-1")
    monkeypatch.setattr(live, "Bridge", FakeBridge)
    monkeypatch.setattr(live, "run_replay", lambda trace, prefs, insulin, variant, bridge, **kw: fake_cycles(variant))
    server = start()
    try:
        run = fetch_json(server, "/api/whatif/run", "POST", {
            "trace_id": trace_id,
            "mode": "replay",
            "variants": [{"label": "A", "algorithm": "autoisf", "smb": True}],
            "options": {},
        })
        frames = read_stream(server, run["run_id"])
    finally:
        server.shutdown()
        live.TRACES.pop(trace_id, None)
        live.WHATIF_SETTINGS.pop(trace_id, None)
    assert [frame["type"] for frame in frames] == ["start", "variant", "cycle", "done"]
    assert frames[0]["mode"] == "replay"
    assert frames[1]["label"] == "A"
    assert frames[2]["glucose"] == 120.0
    metrics = frames[3]["results"][0]["metrics"]
    assert metrics["smb_units"] == 0.2
    assert metrics["cycles"] == 1
    assert "tir_70_180" not in metrics
    assert "tbr_70" not in metrics


def test_replay_run_requires_a_settings_export():
    trace_id = register("run-2", prefs_loaded=False)
    server = start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch_json(server, "/api/whatif/run", "POST", {
                "trace_id": trace_id,
                "mode": "replay",
                "variants": [{"label": "A"}],
                "options": {},
            })
        assert exc.value.code == 400
        assert "settings export" in exc.value.read().decode()
    finally:
        server.shutdown()
        live.TRACES.pop(trace_id, None)
        live.WHATIF_SETTINGS.pop(trace_id, None)


def test_delete_removes_the_import():
    trace_id = register("gone-1")
    server = start()
    try:
        assert fetch_json(server, f"/api/whatif/trace/{trace_id}", "DELETE") == {"deleted": True}
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch_json(server, f"/api/whatif/trace/{trace_id}")
        assert exc.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as exc:
            fetch_json(server, f"/api/whatif/trace/{trace_id}", "DELETE")
        assert exc.value.code == 404
    finally:
        server.shutdown()
        live.TRACES.pop(trace_id, None)
        live.WHATIF_SETTINGS.pop(trace_id, None)


def test_prune_drops_expired_imports():
    trace_id = register("old-1")
    live.WHATIF_SETTINGS[trace_id]["created"] = time.time() - live.WHATIF_TTL_S - 1
    live._prune_whatif(time.time())
    assert trace_id not in live.TRACES
    assert trace_id not in live.WHATIF_SETTINGS
