"""Compression-low artefacts: false dips in the CGM, true glucose untouched."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from cgm.compression import CompressionArtefacts, CompressionConfig
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
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


def test_compression_dip_starts_in_band_and_reaches_the_nadir():
    artefacts = CompressionArtefacts(
        CompressionConfig(rate_per_30d=300.0), seed=1, cycles=288, cadence_min=5.0,
    )
    offsets = [artefacts.offset(index, 100.0) for index in range(288)]
    assert artefacts.events >= 1
    assert min(offsets) <= -26.0


def test_compression_is_deterministic():
    a = CompressionArtefacts(
        CompressionConfig(rate_per_30d=300.0), seed=7, cycles=288, cadence_min=5.0,
    )
    b = CompressionArtefacts(
        CompressionConfig(rate_per_30d=300.0), seed=7, cycles=288, cadence_min=5.0,
    )
    assert [a.offset(i, 100.0) for i in range(288)] == [b.offset(i, 100.0) for i in range(288)]


def test_no_dip_when_the_baseline_is_outside_the_band():
    artefacts = CompressionArtefacts(
        CompressionConfig(rate_per_30d=300.0), seed=3, cycles=288, cadence_min=5.0,
    )
    assert all(artefacts.offset(index, 200.0) == 0.0 for index in range(288))
    assert artefacts.events == 0


def test_compression_reaches_the_algorithm_but_not_the_true_glucose():
    subject = load_subject("adult#001")
    profile = AlgorithmProfile(
        sens=50.0, carb_ratio=10.0, target_bg=110.0, current_basal=1.0, enable_auto_isf=False,
    )
    scenario = Scenario(
        duration_min=24 * 60,
        compression=CompressionConfig(rate_per_30d=300.0),
    )
    bridge = FakeBridge()
    init_bridge(
        bridge, subject, {},
        {"label": "x", "endTime": 8 * 3600 * 1000, "peakTime": 60 * 60 * 1000, "concentration": 1.0},
        profile, scenario,
    )
    loop = run_closed_loop(subject, bridge, profile, scenario, seed=1, noise_kind="none")
    assert min(loop.cgm) < 70.0
    assert min(loop.glucose) > 80.0
    assert min(loop.cgm) < min(loop.glucose) - 20.0
    readings = [value for item in bridge.requests if item["cmd"] == "cycle" for _, value in item["readings"]]
    assert min(readings) < 70.0
