"""Report wiring: compression is opt-in and sourced."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.fidelity_report import (
    compression_for, eligible_names, meal_plan_for, pack_traces, per_subject_stats,
    profile_for, safety_from_loop,
)


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


def test_per_subject_stats_keeps_the_subject_label_and_every_signature():
    import numpy as np
    from runner.fidelity import SIGNATURES, trace

    t = np.arange(0, 22 * 24 * 3600, 300.0)
    item = trace(t, np.full_like(t, 120.0))
    item["subject"] = "adult#001"
    rows = per_subject_stats([item])
    assert rows[0]["subject"] == "adult#001"
    assert rows[0]["cv"] == 0.0
    assert set(rows[0]) == {"subject"} | {key for key, _ in SIGNATURES}


def test_safety_from_loop_reports_gate_metrics():
    from types import SimpleNamespace
    import numpy as np

    loop = SimpleNamespace(
        glucose=np.full(288, 100.0), dose_events=[], cadence_min=5.0,
    )
    row = safety_from_loop(loop)
    assert row["tir_70_180"] == 1.0
    assert row["tbr_70"] == 0.0
    assert row["minutes_below_54_per_day"] == 0.0
    assert row["longest_below_54_min"] == 0.0


def test_pack_traces_names_each_field_by_subject():
    import numpy as np

    trace = {
        "subject": "adult#001",
        "raw": {
            "time": [60.0, 120.0], "cgm": [100.0, 101.0], "glucose": [99.0, 100.0],
            "rescue": [[60.0, 15.0]], "meals": [[420.0, 50.0]], "snacks": [],
            "dose_time": [60.0], "dose_units": [0.1],
        },
    }
    packed = pack_traces([trace])
    assert packed["adult#001:time"].shape == (2,)
    assert packed["adult#001:rescue"].shape == (1, 2)
    np.testing.assert_allclose(packed["adult#001:dose_units"], [0.1])


def test_profile_for_refuses_a_subject_without_a_feasible_cr():
    import pytest
    from simcore.params import load_subject

    with pytest.raises(ValueError):
        profile_for(load_subject("child#001"))


def test_eligible_names_skip_infeasible_profiles_by_default():
    from simcore.params import load_subjects

    all_subjects = load_subjects()
    subset = {name: all_subjects[name] for name in ("child#001", "adult#001")}
    strict = eligible_names(subset)
    assert "child#001" not in strict
    assert "adult#001" in strict
    allowed = eligible_names(subset, allow_fallback=True)
    assert "child#001" in allowed
