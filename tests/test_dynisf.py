"""DynISF ISF command: profile-sens mode returns the profile ISF at target and falls with BG."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from bridge.client import Bridge, DEFAULT_CLASSPATH, insulin_from_settings, load_prefs

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not DEFAULT_CLASSPATH.exists(), reason="bridge classpath not built")


def _sens(bridge, now: int, bg: float, isf: float = 50.0) -> float:
    readings = [[now - 5 * 60_000 * i, bg] for i in range(12, 0, -1)]
    return float(bridge.request({
        "cmd": "dynisf", "now": now, "readings": readings, "sourceSensor": "Libre2",
        "isf": isf, "carbRatio": 10.0, "target": 100.0, "currentBasal": 1.0, "tdd": 40.0,
    })["sens"])


def test_dynisf_returns_profile_isf_at_target() -> None:
    prefs = load_prefs(ROOT / "data/raw/settings.json")
    insulin = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
    now = 1_790_812_800_000 + 10 * 3_600_000
    with Bridge() as bridge:
        bridge.request({
            "cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin, "treatments": [],
            "basal": [[1_790_812_800_000, 1.0]], "timezone": "Europe/Moscow",
        })
        assert _sens(bridge, now, 100.0) == pytest.approx(50.0, rel=1e-6)
        high = _sens(bridge, now, 200.0)
        assert high < 50.0
        assert _sens(bridge, now, 250.0) < high
