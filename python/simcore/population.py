"""Cohort utilities: CR by the published definition, TDI/CF by the 1700 rule, summaries.

CR is computed with the S2013 definition (Dalla Man 2014, section "Determination of CR and CF"):
50 g CHO from basal, an optimal bolus such that (1) glucose at 3 h is 85-110 % of basal,
(2) minimum glucose is above 90 mg/dL, (3) maximum is 40-80 mg/dL above basal.
Among the boluses satisfying all three, the one whose 3 h glucose is closest to basal is used.

The derived cohort is S2008/S2013, not the S2017 population: see PROVENANCE.md.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import Bolus, Meal, Schedule, simulate
from .params import Subject, load_subjects

DIET_G = {"adult": 180.0, "adolescent": 180.0, "child": 135.0}


def group_of(subject: Subject) -> str:
    return subject.name.split("#")[0]


def compute_cr(
    subject: Subject,
    meal_g: float = 50.0,
    grid: np.ndarray | None = None,
    rule: str = "s2008",
) -> float | None:
    """CR (g/U) from the published definition.

    s2008: largest bolus for 50 g that never drops glucose below 95 % of fasting (Kovatchev 2009).
    s2013: bolus with 3 h glucose 85-110 % of basal, min > 90, max 40-80 above basal
           (Dalla Man 2014); may be infeasible for the derived cohort.
    """
    gb = subject.gb
    if grid is None:
        grid = np.arange(1.0, 20.01, 0.5)
    best = None
    for bolus in grid:
        schedule = Schedule(
            basal_u_per_min=subject.basal_u_per_min,
            meals=[Meal(time=30.0, grams=meal_g)],
            boluses=[Bolus(time=30.0, units=float(bolus))],
        )
        trace = simulate(subject, schedule, t_end=270.0, dt=1.0)
        g = trace.glucose
        g3h = float(g[210])
        minimum = float(g.min())
        maximum = float(g.max())
        if rule == "s2008":
            ok = minimum >= 0.95 * gb
            score = float(bolus)
        elif rule == "s2013":
            ok = (
                0.85 * gb <= g3h <= 1.10 * gb
                and minimum > 90.0
                and gb + 40.0 <= maximum <= gb + 80.0
            )
            score = -abs(g3h - gb)
        else:
            raise ValueError(f"unknown CR rule {rule!r}")
        if ok and (best is None or score > best[0]):
            best = (score, float(bolus))
    return None if best is None else meal_g / best[1]


def compute_tdi(subject: Subject, cr: float | None) -> float | None:
    if cr is None or cr <= 0:
        return None
    basal = subject.basal_u_per_min * 24 * 60
    return basal + DIET_G[group_of(subject)] / cr


def compute_cf(tdi: float | None) -> float | None:
    return None if not tdi else 1700.0 / tdi


def _nadir_drop(subject: Subject, units: float, t_end: float) -> float:
    schedule = Schedule(basal_u_per_min=subject.basal_u_per_min, boluses=[Bolus(0.0, units)])
    glucose = simulate(subject, schedule, t_end=t_end, dt=1.0).glucose
    return float(glucose[0] - glucose.min())


def compute_isf_model(subject: Subject, units: float = 1.0, t_end: float = 480.0, max_drop: float = 60.0) -> float:
    """CF by definition: the fall in glucose per unit of insulin at steady state.

    A bolus is given on top of the subject's own basal and the trace is followed for `t_end`
    minutes; the result is (G(0) - min G) / units, mg/dL per U. The probe is halved while the nadir
    drop exceeds `max_drop` so the measurement stays in the near-linear range. This is the model's
    own correction factor, not the empirical 1700/TDI rule: for the derived cohort the rule gives
    2-3x larger values (see docs/acceptance/A3.md).
    """
    drop = _nadir_drop(subject, units, t_end)
    while drop > max_drop and units > 0.25:
        units /= 2.0
        drop = _nadir_drop(subject, units, t_end)
    return float(drop / units)


@dataclass
class CohortRow:
    name: str
    group: str
    weight_kg: float
    tdi: float | None
    tdi_per_kg: float | None
    cr: float | None
    cf: float | None
    fasting_bg: float


def cohort_rows(subjects: dict[str, Subject] | None = None) -> list[CohortRow]:
    subjects = subjects or load_subjects()
    rows: list[CohortRow] = []
    for name in sorted(subjects):
        subject = subjects[name]
        cr = compute_cr(subject, rule="s2008")
        tdi = compute_tdi(subject, cr)
        rows.append(
            CohortRow(
                name=name,
                group=group_of(subject),
                weight_kg=subject.weight_kg,
                tdi=tdi,
                tdi_per_kg=None if tdi is None else tdi / subject.weight_kg,
                cr=cr,
                cf=compute_cf(tdi),
                fasting_bg=subject.gb,
            )
        )
    return rows


def summary(rows: list[CohortRow], group: str, field: str) -> dict[str, float] | None:
    values = [getattr(row, field) for row in rows if row.group == group and getattr(row, field) is not None]
    if not values:
        return None
    array = np.array(values, dtype=float)
    return {
        "n": float(array.size),
        "mean": float(array.mean()),
        "sd": float(array.std(ddof=1)) if array.size > 1 else 0.0,
        "min": float(array.min()),
        "max": float(array.max()),
    }
