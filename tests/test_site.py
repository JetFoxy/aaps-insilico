"""Infusion-site failure tests: the window and the absorbed-insulin split."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.site import SiteFailure
from simcore.params import load_subject


class FakeBridge:
    def __init__(self):
        self.requests: list[dict] = []

    def request(self, payload: dict) -> dict:
        self.requests.append(payload)
        if payload["cmd"] == "init":
            return {"ok": True}
        return {
            "glucose": {
                "glucose": {
                    "glucose": 100.0,
                    "delta": 0.0,
                    "shortAvgDelta": 0.0,
                    "longAvgDelta": 0.0,
                    "date": payload["now"],
                    "duraISFminutes": 0.0,
                    "duraISFaverage": 100.0,
                    "parabolaMinutes": 0.0,
                    "bgAcceleration": 0.0,
                    "a0": 100.0,
                    "a1": 0.0,
                    "a2": 0.0,
                    "corrSqu": 0.0,
                }
            },
            "iob": {"iob": [{"iob": 0.0}]},
            "algorithm": None,
            "meal": None,
            "rt": {"rate": None, "duration": None, "units": 0.0, "insulinReq": 0.0, "reason": ""},
        }


def test_fraction_only_inside_the_window():
    site = SiteFailure(start_min=60.0, duration_min=120.0, residual=0.3)
    assert site.fraction(59.0) == 1.0
    assert site.fraction(60.0) == 0.3
    assert site.fraction(179.0) == 0.3
    assert site.fraction(180.0) == 1.0


def test_invalid_site_failure_is_rejected():
    with pytest.raises(ValueError):
        SiteFailure(start_min=0.0, duration_min=0.0)
    with pytest.raises(ValueError):
        SiteFailure(start_min=0.0, duration_min=60.0, residual=1.5)


def _run(residual: float):
    subject = load_subject("adult#001")
    profile = AlgorithmProfile(
        sens=50.0, carb_ratio=10.0, target_bg=110.0, current_basal=1.0, enable_auto_isf=False,
    )
    scenario = Scenario(
        duration_min=6 * 60,
        site_failure=SiteFailure(start_min=0.0, duration_min=6 * 60, residual=residual),
    )
    bridge = FakeBridge()
    init_bridge(
        bridge, subject, {},
        {"label": "x", "endTime": 8 * 3600 * 1000, "peakTime": 60 * 60 * 1000, "concentration": 1.0},
        profile, scenario,
    )
    return run_closed_loop(subject, bridge, profile, scenario, seed=1, noise_kind="none")


def test_failed_site_raises_glucose_without_changing_the_pump_log():
    control = _run(1.0)
    failed = _run(0.0)
    assert max(failed.glucose) > max(control.glucose) + 10.0
    assert failed.dose_events
