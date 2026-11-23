"""Nightscout client tests: query shape, auth fallback, and secret redaction."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nightscout.client import NightscoutClient, NightscoutError
from nightscout_stub import StubNightscout


def test_entries_use_epoch_filter_and_token():
    stub = StubNightscout({"/api/v1/entries.json": lambda q: (200, [{"sgv": 100}])})
    with stub:
        client = NightscoutClient(stub.url, token="tok-123")
        assert client.entries(1_700_000_000_000, 1_700_086_400_000) == [{"sgv": 100}]
    path, query = stub.requests[0]
    assert path == "/api/v1/entries.json"
    assert query["find[date][$gte]"] == "1700000000000"
    assert query["find[type]"] == "sgv"
    assert query["token"] == "tok-123"


def test_token_never_appears_in_error():
    stub = StubNightscout({"/api/v1/entries.json": lambda q: (401, {"status": 401})})
    with stub:
        client = NightscoutClient(stub.url, token="tok-secret")
        with pytest.raises(NightscoutError) as exc:
            client.entries(0, 1)
    assert "tok-secret" not in str(exc.value)
    assert exc.value.status == 401


def test_error_body_echoing_token_is_redacted():
    body = {"error": "bad token=tok-secret", "echo": "tok-secret"}
    stub = StubNightscout({"/api/v1/entries.json": lambda q: (500, body)})
    with stub:
        client = NightscoutClient(stub.url, token="tok-secret")
        with pytest.raises(NightscoutError) as exc:
            client.entries(0, 1)
    assert "tok-secret" not in str(exc.value)
    assert exc.value.status == 500


def test_base_url_drops_query_and_fragment():
    stub = StubNightscout({"/api/v1/status.json": lambda q: (200, {"status": "ok"})})
    with stub:
        client = NightscoutClient(f"{stub.url}/?token=tok-secret#frag", token="tok-secret")
        assert client.base_url == stub.url
        assert "tok-secret" not in client.base_url
        assert client.status() == {"status": "ok"}


def test_base_url_with_control_character_is_rejected():
    with pytest.raises(NightscoutError) as exc:
        NightscoutClient("http://127.0.0.1:1/nightscout\x00?token=tok-secret", token="tok-secret")
    assert "tok-secret" not in str(exc.value)
    assert "127.0.0.1" not in str(exc.value)


def test_invalid_url_error_carries_no_token(monkeypatch):
    client = NightscoutClient("http://127.0.0.1:1", token="tok-secret")
    monkeypatch.setattr(client, "base_url", "http://127.0.0.1:1/bad path")
    with pytest.raises(NightscoutError) as exc:
        client.entries(0, 1)
    assert "tok-secret" not in str(exc.value)


def test_url_without_scheme_error_carries_no_token():
    client = NightscoutClient("example.test", token="tok-secret")
    with pytest.raises(NightscoutError) as exc:
        client.status()
    assert "tok-secret" not in str(exc.value)


def test_api_secret_fallback_on_401():
    calls = []

    def route(query):
        calls.append(dict(query))
        return (401, {}) if "token" in query else (200, [{"sgv": 101}])

    stub = StubNightscout({"/api/v1/entries.json": route})
    with stub:
        client = NightscoutClient(stub.url, token="tok", api_secret="abc")
        assert client.entries(0, 1) == [{"sgv": 101}]
    assert "api-secret" in stub.requests[-1][1]
    assert stub.requests[-1][1]["api-secret"] == hashlib.sha1(b"abc").hexdigest()
