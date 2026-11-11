"""Extended cohort and shared CLI options."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.options import _coerce, apply_prefs
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
