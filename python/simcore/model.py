"""S2017-like glucose-insulin core (Visentin et al. 2018, Appendix).

The equations follow the Appendix of the S2017 paper, with the parameter vector taken from a
**derived** open dataset (simglucose, S2008/S2013). Time-varying hooks (`kp1`, `kp3`, `Vmx`,
`kir`), the hypoglycaemia risk term and the subcutaneous delay `tau` are present but inactive by
default: their parameters are not published (see docs/registry/parameters.md §3.3, §5).

Not an official UVA/Padova simulator, not a clinical model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.integrate import solve_ivp

from .params import Subject
from exercise.model import ExerciseSession

MG_PER_G = 1000.0
U_TO_PMOL = 6000.0  # U/min -> pmol/kg/min after dividing by BW, as in the reference model


@dataclass(frozen=True)
class Meal:
    time: float  # min
    grams: float
    eat_rate_g_per_min: float = 5.0

    @property
    def duration(self) -> float:
        return self.grams / self.eat_rate_g_per_min


@dataclass(frozen=True)
class Bolus:
    time: float  # min
    units: float
    duration: float = 1.0


@dataclass
class Schedule:
    basal_u_per_min: float
    meals: list[Meal] = field(default_factory=list)
    boluses: list[Bolus] = field(default_factory=list)

    def intake_mg_per_min(self, t: float) -> float:
        rate = 0.0
        for meal in self.meals:
            if meal.time <= t < meal.time + meal.duration:
                rate += meal.eat_rate_g_per_min * MG_PER_G
        return rate

    def meal_dbar_mg(self, t: float) -> float:
        """Total size of the most recent meal still in progress, for the kempt() shape."""
        active = [m for m in self.meals if m.time <= t < m.time + m.duration]
        if active:
            return max(m.grams for m in active) * MG_PER_G
        return 0.0

    def insulin_u_per_min(self, t: float) -> float:
        rate = self.basal_u_per_min
        for bolus in self.boluses:
            if bolus.time <= t < bolus.time + bolus.duration:
                rate += bolus.units / bolus.duration
        return rate


@dataclass
class TimeVarying:
    """Time-varying terms. Defaults reproduce the time-invariant S2013 behaviour.

    si_multipliers: (breakfast, lunch, dinner) multipliers on SI, segments
    4:00-11:00, 11:00-17:00, 17:00-4:00 (Visentin 2015).
    dawn_egp: additive rise of kp1, mg/kg/min, linear 3:00-7:00 (Mallad via Visentin 2018).
    dawn_kir: kir multiplier reached at 7:00 (1.0 = no dawn insulin resistance).
    clock_offset_min: model time t=0 corresponds to this clock minute (default 00:00).
    """

    kp1_factor: float = 1.0
    kp3_factor: float = 1.0
    vmx_factor: float = 1.0
    kir: float = 1.0
    risk_r1: float = 0.0
    risk_r2: float = 0.0
    gth: float = 60.0
    si_multipliers: tuple[float, float, float] | None = None
    exercise: ExerciseSession | None = None
    ka_multiplier: float = 1.0
    dawn_egp: float = 0.0
    dawn_kir: float = 1.0
    clock_offset_min: float = 0.0

    def ka_scale(self, t: float) -> float:
        if self.exercise is None or self.ka_multiplier == 1.0:
            return 1.0
        return self.ka_multiplier if self.exercise.active(t) else 1.0

    def clock(self, t: float) -> float:
        return (t + self.clock_offset_min) % 1440.0

    def si(self, t: float) -> float:
        if self.si_multipliers is None:
            return 1.0
        minute = self.clock(t)
        if 240.0 <= minute < 660.0:
            return self.si_multipliers[0]
        if 660.0 <= minute < 1020.0:
            return self.si_multipliers[1]
        return self.si_multipliers[2]

    def dawn_ramp(self, t: float) -> float:
        minute = self.clock(t)
        if minute <= 180.0:
            return 0.0
        if minute >= 420.0:
            return 1.0
        return (minute - 180.0) / 240.0


class Core:
    def __init__(self, subject: Subject, schedule: Schedule, time_varying: TimeVarying | None = None):
        self.subject = subject
        self.schedule = schedule
        self.tv = time_varying or TimeVarying()
        self.p = subject.params

    def kempt(self, qsto: float, dbar: float) -> float:
        p = self.p
        if dbar <= 0.0:
            return p["kmax"]
        aa = 5.0 / (2.0 * dbar * (1.0 - p["b"]))
        cc = 5.0 / (2.0 * dbar * p["d"])
        return p["kmin"] + (p["kmax"] - p["kmin"]) / 2.0 * (
            np.tanh(aa * (qsto - p["b"] * dbar)) - np.tanh(cc * (qsto - p["d"] * dbar)) + 2.0
        )

    def risk(self, g_mgdl: float) -> float:
        tv, gb = self.tv, self.subject.gb
        if tv.risk_r1 == 0.0 or g_mgdl >= gb:
            return 0.0
        def f(value: float) -> float:
            return np.log(value) ** tv.risk_r2 - np.log(gb) ** tv.risk_r2
        if g_mgdl >= tv.gth:
            return 10.0 * f(g_mgdl) ** 2
        return 10.0 * f(tv.gth) ** 2

    def rhs(self, t: float, x: np.ndarray) -> np.ndarray:
        p, tv = self.p, self.tv
        dx = np.zeros(13)

        qsto = x[0] + x[1]
        dbar = self.schedule.meal_dbar_mg(t)
        kgut = self.kempt(qsto, dbar)
        dx[0] = -p["kmax"] * x[0] + self.schedule.intake_mg_per_min(t)
        dx[1] = p["kmax"] * x[0] - kgut * x[1]
        dx[2] = kgut * x[1] - p["kabs"] * x[2]

        rat = p["f"] * p["kabs"] * x[2] / p["BW"]
        ramp = tv.dawn_ramp(t)
        si = tv.si(t) * (tv.exercise.si_multiplier(t) if tv.exercise is not None else 1.0)
        egp = (p["kp1"] * tv.kp1_factor + tv.dawn_egp * ramp) - p["kp2"] * x[3] - p["kp3"] * tv.kp3_factor * si * x[8]
        uii = p["Fsnc"]
        e = p["ke1"] * (x[3] - p["ke2"]) if x[3] > p["ke2"] else 0.0

        dx[3] = max(egp, 0.0) + rat - uii - e - p["k1"] * x[3] + p["k2"] * x[4]
        dx[3] = (x[3] >= 0) * dx[3]

        g_mgdl = x[3] / p["Vg"]
        kir = tv.kir * (1.0 + (tv.dawn_kir - 1.0) * ramp)
        vmt = (p["Vm0"] + p["Vmx"] * tv.vmx_factor * si * x[6]) * kir * (1.0 + tv.risk_r1 * self.risk(g_mgdl))
        uid = vmt * x[4] / (p["Km0"] + x[4])
        dx[4] = -uid + p["k1"] * x[3] - p["k2"] * x[4]
        dx[4] = (x[4] >= 0) * dx[4]

        ka_scale = tv.ka_scale(t)
        dx[5] = -(p["m2"] + p["m4"]) * x[5] + p["m1"] * x[9] + p["ka1"] * ka_scale * x[10] + p["ka2"] * ka_scale * x[11]
        it = x[5] / p["Vi"]
        dx[5] = (x[5] >= 0) * dx[5]

        dx[6] = -p["p2u"] * x[6] + p["p2u"] * (it - p["Ib"])
        dx[7] = -p["ki"] * (x[7] - it)
        dx[8] = -p["ki"] * (x[8] - x[7])

        dx[9] = -(p["m1"] + p["m30"]) * x[9] + p["m2"] * x[5]
        dx[9] = (x[9] >= 0) * dx[9]

        insulin = self.schedule.insulin_u_per_min(t) * U_TO_PMOL / p["BW"]
        dx[10] = insulin - (p["ka1"] * ka_scale + p["kd"]) * x[10]
        dx[10] = (x[10] >= 0) * dx[10]
        dx[11] = p["kd"] * x[10] - p["ka2"] * ka_scale * x[11]
        dx[11] = (x[11] >= 0) * dx[11]

        dx[12] = -p["ksc"] * x[12] + p["ksc"] * x[3]
        dx[12] = (x[12] >= 0) * dx[12]
        return dx


@dataclass
class Trace:
    t: np.ndarray
    x: np.ndarray

    @property
    def glucose(self) -> np.ndarray:
        return self.x[12] / self.vg

    vg: float = 1.0

    @property
    def glucose_plasma(self) -> np.ndarray:
        return self.x[3] / self.vg

    @property
    def insulin_pmol(self) -> np.ndarray:
        return self.x[5] / self.vi

    vi: float = 1.0


def simulate(
    subject: Subject,
    schedule: Schedule,
    t_end: float,
    dt: float = 1.0,
    time_varying: TimeVarying | None = None,
    x0: np.ndarray | None = None,
) -> Trace:
    core = Core(subject, schedule, time_varying)
    y0 = subject.state0.copy() if x0 is None else np.array(x0, dtype=float)
    t_eval = np.arange(0.0, t_end + 0.5 * dt, dt)
    solution = solve_ivp(
        core.rhs,
        (0.0, t_end),
        y0,
        method="BDF",
        t_eval=t_eval,
        rtol=1e-6,
        atol=1e-9,
        max_step=1.0,
    )
    if not solution.success:
        raise RuntimeError(f"integration failed: {solution.message}")
    trace = Trace(t=solution.t, x=solution.y)
    trace.vg = subject.params["Vg"]
    trace.vi = subject.params["Vi"]
    return trace


def absorbed_carb_mg(subject: Subject, trace: Trace) -> float:
    """Total glucose that left the gut: integral of f * kabs * Qgut / BW over BW (mg)."""
    kabs = subject.params["kabs"]
    f = subject.params["f"]
    qgut = trace.x[2]
    return float(np.trapezoid(f * kabs * qgut, trace.t))
