"""One profile policy: refuse an infeasible CR unless the caller asks for the fallback."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from runner.profiles import eligible_names, isf_for_source, profile_values


def _subset():
    from simcore.params import load_subjects

    all_subjects = load_subjects()
    return {name: all_subjects[name] for name in ("child#001", "adult#001")}


def test_profile_values_refuse_an_infeasible_cr():
    with pytest.raises(ValueError):
        profile_values(_subset()["child#001"])


def test_profile_values_fallback_is_explicit():
    cr, tdi, isf = profile_values(_subset()["child#001"], allow_fallback=True)
    assert (cr, tdi, isf) == (10.5, 40.0, 50.0)


def test_eligible_names_skip_infeasible_profiles_unless_allowed():
    subjects = _subset()
    assert eligible_names(subjects) == ["adult#001"]
    assert eligible_names(subjects, allow_fallback=True) == ["adult#001", "child#001"]


def test_isf_for_source_model_differs_from_the_rule():
    subject = _subset()["adult#001"]
    rule = isf_for_source(subject, "rule")
    model = isf_for_source(subject, "model")
    assert model != rule
    assert model == isf_for_source(subject, "model")
