"""Lispro PK variant: published population draws, mass balance, and the delay."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from simcore.insulin_pk import apply_lispro, lispro_params
from runner.closed_loop import bolus_offset_in_window
from simcore.model import Bolus, Schedule, U_TO_PMOL, simulate
from simcore.params import load_subject


def test_lispro_draws_are_deterministic_and_inside_the_published_ranges():
    a = lispro_params("adult#001", seed=7)
    b = lispro_params("adult#001", seed=7)
    assert a == b
    assert 0.748 <= a["tau"] <= 21.9
    assert 0.000079 <= a["ka1"] <= 0.00824
    assert 0.00418 <= a["ka2"] <= 0.0313
    assert a["kd"] == a["ka2"] + a["alpha"]


def test_lispro_state_keeps_the_subcutaneous_mass_balance():
    subject = load_subject("adult#001")
    variant = apply_lispro(subject, seed=3)
    p = variant.params
    input_pmol = subject.basal_u_per_min * U_TO_PMOL / p["BW"]
    rai = p["ka1"] * variant.state0[10] + p["ka2"] * variant.state0[11]
    assert abs(rai - input_pmol) / input_pmol < 1e-9


def test_bolus_offset_respects_the_delay_and_the_window():
    assert bolus_offset_in_window(0.0, 0.0, 5.0, 6.0) is None
    assert bolus_offset_in_window(0.0, 0.0, 10.0, 6.0) == 6.0
    assert bolus_offset_in_window(0.0, 5.0, 10.0, 6.0) == 1.0
    assert bolus_offset_in_window(0.0, 0.0, 10.0, 0.0) == 0.0


def test_lispro_shortens_the_one_unit_nadir_for_a_slow_subject():
    subject = load_subject("adult#001")
    old = simulate(subject, Schedule(subject.basal_u_per_min, boluses=[Bolus(0.0, 1.0)]), 600, 1.0)
    variant = apply_lispro(subject, seed=1)
    new = simulate(
        variant, Schedule(variant.basal_u_per_min, boluses=[Bolus(0.0, 1.0)]), 600, 1.0,
    )
    assert int(np.argmin(new.glucose)) < int(np.argmin(old.glucose))


def test_plasma_curve_is_linear_in_dose_and_starts_at_zero():
    from simcore.insulin_pk import plasma_curve

    times = np.array([0.0, 30.0, 60.0, 120.0, 240.0])
    kw = dict(tau=6.0, ka1=0.002, ka2=0.013, alpha=0.015, vi=0.135, ke=0.113, bw=70.0)
    one = plasma_curve(1.0, times, **kw)
    two = plasma_curve(2.0, times, **kw)
    assert one[0] == 0.0
    np.testing.assert_allclose(two, 2.0 * one, rtol=1e-10)
    assert one.max() > 0


def test_lyumjev_fit_recovers_a_synthetic_curve():
    from simcore.insulin_pk import LYUMJEV_LABEL, plasma_curve
    from runner.derive_lyumjev import fit_lyumjev

    true = dict(tau=8.0, ka1=0.0025, ka2=0.016, alpha=0.02, vi=0.14, ke=0.113, bw=70.0)
    times = np.array(LYUMJEV_LABEL["times_min"], dtype=float)
    curve = plasma_curve(15.0, times, **true)
    fit = fit_lyumjev(times[1:], curve[1:], dose_u=15.0, ke=0.113, bw=70.0)
    assert fit["ok"]
    for key in ("ka1", "ka2", "alpha", "vi"):
        assert abs(fit[key] - true[key]) / true[key] < 0.5
