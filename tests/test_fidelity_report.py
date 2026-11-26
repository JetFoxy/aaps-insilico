"""Report wiring: compression is opt-in and sourced."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.fidelity_report import compression_for, meal_plan_for


def test_compression_for_is_off_at_zero_and_carries_the_rate():
    assert compression_for(0) is None
    config = compression_for(3.5)
    assert config is not None
    assert config.rate_per_30d == 3.5


def test_meal_plan_for_fixed_and_template():
    fixed = meal_plan_for("fixed", days=2, seed=1, body_weight=70.0)
    assert len(fixed[0]) == 6 and fixed[1] == []
    template = meal_plan_for("template", days=2, seed=1, body_weight=70.0)
    assert template == meal_plan_for("template", days=2, seed=1, body_weight=70.0)
    assert template[0] and template[1]
