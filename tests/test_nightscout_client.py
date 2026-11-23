"""Nightscout client tests: query shape, auth fallback, and secret redaction."""
from __future__ import annotations

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
