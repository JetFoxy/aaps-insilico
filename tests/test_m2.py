"""M2 acceptance tests: derived cohort, CR/TDI/CF, summary consistency."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from simcore.params import load_subject, load_subjects
from simcore.population import compute_cf, compute_cr, compute_tdi, group_of, summary, cohort_rows


def test_cohort_has_30_subjects_in_three_groups() -> None:
    subjects = load_subjects()
    assert len(subjects) == 30
    groups = {group_of(s) for s in subjects.values()}
    assert groups == {"adult", "adolescent", "child"}


def test_cr_is_feasible_for_an_adult() -> None:
    subject = load_subject("adult#001")
    cr = compute_cr(subject)
    assert cr is not None
    assert 3.0 < cr < 40.0


def test_cr_is_deterministic() -> None:
    subject = load_subject("adult#001")
    assert compute_cr(subject) == compute_cr(subject)


def test_tdi_and_cf_are_positive() -> None:
    subject = load_subject("adult#001")
    cr = compute_cr(subject)
    tdi = compute_tdi(subject, cr)
    assert tdi is not None and tdi > 0
    cf = compute_cf(tdi)
    assert cf is not None and cf > 0


def test_fasting_bg_matches_its_source_cohort() -> None:
    """The derived cohort is S2008: adult fasting BG 143.4 (9.33) in Kovatchev 2009, Table 1."""
    subjects = load_subjects()
    adults = [s.gb for name, s in subjects.items() if group_of(s) == "adult"]
    mean = float(np.mean(adults))
    assert abs(mean - 143.4) < 9.33, f"adult fasting BG mean {mean:.1f} vs published 143.4 (9.33)"


def test_isf_from_model_is_positive_and_deterministic() -> None:
    from simcore.population import compute_isf_model

    subject = load_subject("adult#001")
    first = compute_isf_model(subject)
    second = compute_isf_model(subject)
    assert first > 0.0
    assert first == pytest.approx(second)


def test_isf_from_model_matches_manual_probe() -> None:
    from simcore.model import Bolus, Schedule, simulate
    from simcore.population import compute_isf_model

    subject = load_subject("adult#001")
    units = 1.0
    trace = simulate(
        subject, Schedule(basal_u_per_min=subject.basal_u_per_min, boluses=[Bolus(0.0, units)]),
        t_end=480.0, dt=1.0,
    )
    expected = float(trace.glucose[0] - trace.glucose.min())
    assert compute_isf_model(subject) == pytest.approx(expected / units)


def test_isf_from_model_is_smaller_than_the_1700_rule_for_the_cohort() -> None:
    from simcore.population import compute_isf_model

    names = ["adult#001", "adult#004", "adolescent#002", "adolescent#003", "child#002", "child#005"]
    ratios = []
    for name in names:
        subject = load_subject(name)
        cr = compute_cr(subject)
        tdi = compute_tdi(subject, cr)
        if tdi:
            ratios.append(compute_isf_model(subject) / compute_cf(tdi))
    assert np.median(ratios) < 0.7
