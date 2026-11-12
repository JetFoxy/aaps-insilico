"""Extended cohort and shared CLI options."""
from __future__ import annotations

import sys
import dataclasses
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.options import _coerce, apply_prefs
from simcore import population
from simcore.cohort import extend_cohort
from simcore.model import Schedule, simulate
from simcore.params import load_subjects


def test_extended_cohort_adds_copies_and_keeps_names_unique() -> None:
    base = load_subjects()
    extended = extend_cohort(base, copies=2, seed=11)
    assert len(extended) == 3 * len(base)
    assert "adult#001-v01" in extended and "adult#001-v02" in extended
    assert len(set(extended)) == len(extended)


def test_extended_subject_keeps_fasting_steady_state() -> None:
    extended = extend_cohort(copies=1, seed=5)
    for name in ("adult#001-v01", "adolescent#003-v01", "child#005-v01"):
        subject = extended[name]
        trace = simulate(subject, Schedule(basal_u_per_min=subject.basal_u_per_min), 120.0, 1.0)
        assert np.max(np.abs(trace.glucose - subject.gb)) < 1e-6


def test_extended_cohort_is_deterministic() -> None:
    first = extend_cohort(copies=1, seed=9)
    second = extend_cohort(copies=1, seed=9)
    assert first["adult#001-v01"].params == second["adult#001-v01"].params
    assert np.array_equal(first["adult#001-v01"].state0, second["adult#001-v01"].state0)


def test_pref_overrides_coerce_types() -> None:
    prefs = apply_prefs({"a": 1}, ["bgAccel_ISF_weight=0.5", "use_smb=false", "smbinterval=4"])
    assert prefs["bgAccel_ISF_weight"] == pytest.approx(0.5)
    assert prefs["use_smb"] is False
    assert prefs["smbinterval"] == 4
    assert prefs["a"] == 1
    assert _coerce("true") is True


def test_load_residuals_from_csv_and_npy(tmp_path) -> None:
    from runner.options import RESIDUALS, load_residuals

    csv_path = tmp_path / "resid.csv"
    csv_path.write_text("1.0\n-2.0\n3.0\n")
    assert list(load_residuals(str(csv_path))) == [1.0, -2.0, 3.0]

    npy_path = tmp_path / "resid.npy"
    np.save(npy_path, np.array([4.0, 5.0]))
    assert list(load_residuals(str(npy_path))) == [4.0, 5.0]
    assert load_residuals(None) is RESIDUALS


def test_profile_cache_uses_full_subject_identity(monkeypatch) -> None:
    base = load_subjects()["adult#001"]
    copy = extend_cohort({"adult#001": base}, copies=1, seed=7)["adult#001-v01"]
    copy = dataclasses.replace(copy, name=base.name)
    calls = []

    def fake_cr(subject, meal_g, grid, rule):
        calls.append(("cr", subject.params["kabs"]))
        return 10.0 if subject.params["kabs"] == base.params["kabs"] else 11.0

    def fake_isf(subject, units, t_end, max_drop):
        calls.append(("isf", subject.params["kabs"]))
        return 45.0

    monkeypatch.setattr(population, "_compute_cr", fake_cr)
    monkeypatch.setattr(population, "_compute_isf_model", fake_isf)
    population.clear_profile_cache()

    try:
        first = population.cached_subject_profile(base)
        repeated = population.cached_subject_profile(base)
        different = population.cached_subject_profile(copy)

        assert first == repeated
        assert first is not repeated
        assert different is not first
        assert different["cr"] != first["cr"]
        with pytest.raises(TypeError):
            first["cr"] = 11.0
        assert calls == [
            ("cr", base.params["kabs"]),
            ("cr", copy.params["kabs"]),
        ]
    finally:
        population.clear_profile_cache()
