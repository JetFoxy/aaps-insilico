"""M1 acceptance tests: basal steady state, meal mass balance, non-negativity, 60 g response."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from simcore.model import Bolus, Meal, Schedule, absorbed_carb_mg, simulate
from simcore.params import load_subject

ADULT_CR_G_PER_U = 10.5  # S2008 Table 1, adults: the cohort the derived parameters come from


def test_basal_steady_state_holds_gb() -> None:
    subject = load_subject("adult#001")
    schedule = Schedule(basal_u_per_min=subject.basal_u_per_min)
    trace = simulate(subject, schedule, t_end=24 * 60, dt=1.0)
    deviation = np.abs(trace.glucose - subject.gb).max()
    assert deviation < 1.0, f"subcutaneous glucose drifted {deviation:.3f} mg/dL from Gb"


def test_meal_mass_balance() -> None:
    subject = load_subject("adult#001")
    schedule = Schedule(
        basal_u_per_min=subject.basal_u_per_min,
        meals=[Meal(time=60.0, grams=60.0)],
    )
    trace = simulate(subject, schedule, t_end=12 * 60, dt=1.0)
    expected = subject.params["f"] * 60.0 * 1000.0
    absorbed = absorbed_carb_mg(subject, trace)
    error = abs(absorbed - expected) / expected
    assert error < 0.01, f"absorbed {absorbed:.1f} mg vs f*dose {expected:.1f} mg ({error:.3%})"


def test_states_nonnegative() -> None:
    subject = load_subject("adult#001")
    schedule = Schedule(
        basal_u_per_min=subject.basal_u_per_min,
        meals=[Meal(time=60.0, grams=60.0)],
        boluses=[Bolus(time=60.0, units=60.0 / ADULT_CR_G_PER_U)],
    )
    trace = simulate(subject, schedule, t_end=6 * 60, dt=1.0)
    assert trace.x.min() >= -1e-8, f"negative state {trace.x.min():.3e}"


def test_simulate_rejects_unknown_integrator() -> None:
    subject = load_subject("adult#001")
    with pytest.raises(ValueError, match="unknown integration method"):
        simulate(subject, Schedule(basal_u_per_min=subject.basal_u_per_min), 5.0, method="RK45")


def test_60g_response_adults() -> None:
    peaks_glucose = []
    peaks_insulin = []
    for name in ("adult#001", "adult#002", "adult#003", "adult#004", "adult#005"):
        subject = load_subject(name)
        schedule = Schedule(
            basal_u_per_min=subject.basal_u_per_min,
            meals=[Meal(time=60.0, grams=60.0)],
            boluses=[Bolus(time=60.0, units=60.0 / ADULT_CR_G_PER_U)],
        )
        trace = simulate(subject, schedule, t_end=6 * 60, dt=1.0)
        peaks_glucose.append(trace.glucose.max() - subject.gb)
        peaks_insulin.append(trace.insulin_pmol.max())
    mean_glucose = float(np.mean(peaks_glucose))
    mean_insulin = float(np.mean(peaks_insulin))
    print(f"peaks glucose {peaks_glucose} mean {mean_glucose:.1f}; insulin {peaks_insulin} mean {mean_insulin:.1f}")
    # S2017 Table 1 / text: adults breakfast peak 68 +/- 30 mg/dL, insulin 185 +/- 108 pmol/L.
    # The derived cohort is S2008 (Gb ~135-150, CR 10.5), so the peak sits near the upper edge;
    # per-subject CR by the S2013 rule is M2, not M1.
    assert 38.0 <= mean_glucose <= 98.0, f"mean glucose peak {mean_glucose:.1f} outside 68+/-30"
    assert 77.0 <= mean_insulin <= 293.0, f"mean insulin peak {mean_insulin:.1f} outside 185+/-108"
