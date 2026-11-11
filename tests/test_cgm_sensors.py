"""Published CGM sensor-error models (Facchinetti 2014, Vettoretti 2019)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from cgm.sensors import make_sensor


def _trace(kind: str, seed: int) -> list[float]:
    sensor = make_sensor(kind, seed)
    return [sensor.measure(100.0, t) for t in range(5, 125, 5)]


def test_sensor_models_are_deterministic_by_seed() -> None:
    for kind in ("facchinetti", "dexcom"):
        assert _trace(kind, 3) == _trace(kind, 3)
        assert _trace(kind, 3) != _trace(kind, 4)


def test_sensor_models_return_finite_values_near_the_ig_level() -> None:
    for kind in ("facchinetti", "dexcom"):
        values = _trace(kind, 0)
        assert all(math.isfinite(value) for value in values)
        assert 60.0 < sum(values) / len(values) < 140.0


def test_unknown_kind_returns_none() -> None:
    assert make_sensor("residuals", 0) is None
