"""Closed loop with meals, both meal modes, SMB, dose journal and simple failures."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from cgm.noise import AR1Noise, BlockNoise, WhiteNoise
from exercise.model import ExerciseSession
from pump.pump import Pump, PumpConfig
from simcore.model import Bolus, Meal, Schedule, TimeVarying, simulate
from simcore.params import Subject

EPOCH0_MS = 1_790_812_800_000  # 2026-10-01T00:00:00Z, synthetic scenario clock


@dataclass
class AlgorithmProfile:
    sens: float
    carb_ratio: float
    target_bg: float
    max_iob: float = 12.0
    max_basal: float = 4.0
    current_basal: float = 0.0
    smb_interval: int = 3
    bolus_increment: float = 0.05
    enable_auto_isf: bool = False
    smb_ratio_3min: float = 0.3


@dataclass
class Scenario:
    name: str = "basal"
    duration_min: int = 24 * 60
    meals: list[tuple[float, float]] = field(default_factory=list)  # (minute, grams)
    meal_mode: str = "fcl"  # "fcl" | "announced"
    exercise: ExerciseSession | None = None
    suspend: tuple[float, float] | None = None  # (start_min, duration_min)
    cgm_gap: tuple[float, float] | None = None
    cgm_bias: tuple[float, float, float] | None = None  # (start_min, duration_min, mg/dL)
    si_multipliers: tuple[float, float, float] | None = None
    dawn_egp: float = 0.0


@dataclass
class LoopResult:
    time: list[float] = field(default_factory=list)
    cgm: list[float] = field(default_factory=list)
    glucose: list[float] = field(default_factory=list)
    rate: list[float] = field(default_factory=list)
    insulin_req: list[float] = field(default_factory=list)
    variable_sens: list[float] = field(default_factory=list)
    current_temp: list[dict] = field(default_factory=list)
    iob: list[float] = field(default_factory=list)
    cob: list[float] = field(default_factory=list)
    reason: list[str] = field(default_factory=list)
    bolus_units_to_model: float = 0.0
    dose_events: list[tuple[float, float, str]] = field(default_factory=list)


def smb_ratio_for_cadence(profile: AlgorithmProfile, cadence_min: float) -> float:
    """Scale the per-3-min SMB delivery ratio so insulin per minute stays constant."""
    return profile.smb_ratio_3min * cadence_min / 3.0


def init_bridge(bridge, subject: Subject, prefs: dict, insulin: dict, profile: AlgorithmProfile, scenario: Scenario) -> None:
    treatments: list[dict] = []
    if scenario.meal_mode == "announced":
        for time_min, grams in scenario.meals:
            treatments.append({
                "kind": "carbs", "ts": EPOCH0_MS + int(time_min * 60_000),
                "amount": grams, "duration": 0,
            })
    bridge.request({
        "cmd": "init", "prefs": prefs, "profiles": [], "insulin": insulin,
        "treatments": treatments,
        "basal": [[EPOCH0_MS, profile.current_basal]],
        "timezone": "Europe/Moscow",
    })


def run_closed_loop(
    subject: Subject,
    bridge,
    profile: AlgorithmProfile,
    scenario: Scenario | None = None,
    cadence_min: float = 5.0,
    seed: int = 0,
    residuals: np.ndarray | None = None,
    pump_config: PumpConfig | None = None,
    micro_bolus_allowed: bool = True,
    temp_mode: str = "planned",
    on_cycle: Callable[[LoopResult], None] | None = None,
    noise_kind: str = "residuals",
    noise_scale: float = 1.0,
    noise_sigma: float = 2.49,
    noise_rho: float = 0.8,
) -> LoopResult:
    """temp_mode="planned" runs 30-min temps and reports minutes remaining; "cycle" is the legacy
    one-temp-per-cycle approximation."""
    scenario = scenario or Scenario()
    pump = Pump(pump_config or PumpConfig())
    if noise_kind == "residuals" and residuals is not None:
        noise = BlockNoise(residuals, seed=seed, scale=noise_scale)
    elif noise_kind == "ar1":
        noise = AR1Noise(noise_rho, noise_sigma * noise_scale, seed=seed)
    elif noise_kind == "white":
        noise = WhiteNoise(noise_sigma * noise_scale, seed=seed)
    elif noise_kind == "none":
        noise = None
    else:
        raise ValueError(f"unknown noise kind {noise_kind!r}")
    cycles = int(scenario.duration_min // cadence_min)
    noise_values = noise.sample(cycles) if noise is not None else np.zeros(cycles)

    state = subject.state0.copy()
    basal_u_per_h = profile.current_basal
    cgm_history: list[list[float]] = []
    applied_temps: list[dict] = []
    applied_boluses: list[dict] = []
    pending_boluses: list[tuple[float, float]] = []
    result = LoopResult()

    planned_temps = temp_mode == "planned"
    temp_rate: float | None = None
    temp_start = 0.0
    temp_end = 0.0

    def in_window(window: tuple[float, float] | None, t: float) -> bool:
        return window is not None and window[0] <= t < window[0] + window[1]

    for cycle in range(cycles):
        t0 = cycle * cadence_min
        t1 = t0 + cadence_min
        suspended_now = scenario.suspend is not None and in_window(scenario.suspend, t0)

        if planned_temps:
            if temp_rate is not None and t0 < temp_end:
                segment_rate = temp_rate
            else:
                temp_rate = None
                segment_rate = basal_u_per_h
            if suspended_now:
                segment_rate = 0.0
                temp_rate = None
        else:
            segment_rate = 0.0 if suspended_now else basal_u_per_h

        meals = [
            Meal(time=0.0, grams=grams, eat_rate_g_per_min=grams / cadence_min)
            for time_min, grams in scenario.meals
            if time_min <= t0 < time_min + cadence_min
        ]
        exercise = None
        if scenario.exercise is not None:
            exercise = ExerciseSession(
                start_min=scenario.exercise.start_min - t0,
                duration_min=scenario.exercise.duration_min,
                intensity=scenario.exercise.intensity,
                si_gain=scenario.exercise.si_gain,
                recovery_min=scenario.exercise.recovery_min,
            )
        time_varying = TimeVarying(
            exercise=exercise, clock_offset_min=t0,
            si_multipliers=scenario.si_multipliers, dawn_egp=scenario.dawn_egp,
        )
        boluses = []
        still_pending: list[tuple[float, float]] = []
        for deliver_at, units in pending_boluses:
            if deliver_at < t1:
                boluses.append(Bolus(
                    time=max(0.0, deliver_at - t0), units=units,
                    duration=pump.config.bolus_duration_min,
                ))
                result.bolus_units_to_model += units
            else:
                still_pending.append((deliver_at, units))
        pending_boluses = still_pending
        segment = simulate(
            subject, Schedule(segment_rate / 60.0, meals, boluses), cadence_min, 0.5,
            time_varying=time_varying, x0=state,
        )
        state = segment.x[:, -1]
        g_plasma = float(segment.x[3, -1] / subject.params["Vg"])
        g_sub = float(segment.x[12, -1] / subject.params["Vg"])

        if scenario.cgm_gap is not None and in_window(scenario.cgm_gap, t0):
            value = cgm_history[-1][1] if cgm_history else g_sub
        else:
            bias = 0.0
            if scenario.cgm_bias is not None and in_window(scenario.cgm_bias, t0):
                bias = scenario.cgm_bias[2]
            value = float(np.clip(np.round(g_sub + noise_values[cycle] + bias), 39.0, 400.0))
            cgm_history.append([EPOCH0_MS + int(t1 * 60_000), value])

        now_ms = EPOCH0_MS + int(t1 * 60_000)
        glucose = bridge.request({
            "cmd": "glucose", "now": now_ms, "readings": cgm_history, "sourceSensor": "Libre2",
        })["glucose"]

        rate_now = basal_u_per_h
        insulin_req = 0.0
        variable_sens = profile.sens
        iob_now = 0.0
        cob_now = 0.0
        reason_text = ""
        if planned_temps:
            if temp_rate is not None and t1 < temp_end:
                current_temp = {
                    "duration": int(math.ceil(temp_end - t1)),
                    "rate": temp_rate,
                    "minutesrunning": int(t1 - temp_start),
                }
            else:
                current_temp = {"duration": 0, "rate": 0.0, "minutesrunning": 0}
        else:
            current_temp = {"duration": int(cadence_min), "rate": basal_u_per_h, "minutesrunning": 0}

        if glucose is not None:
            treatments = applied_temps + applied_boluses
            iob = bridge.request({
                "cmd": "iob", "now": now_ms, "autosens": 1.0,
                "exerciseMode": False, "halfBasalExerciseTarget": 160, "isTempTarget": False,
                "treatments": treatments,
            })["iob"]
            iob_now = float(iob[0]["iob"])

            if profile.enable_auto_isf:
                auto = bridge.request({
                    "cmd": "autoisf", "now": now_ms,
                    "readings": cgm_history, "sourceSensor": "Libre2",
                    "isf": profile.sens, "carbRatio": profile.carb_ratio,
                    "target": profile.target_bg, "targetLow": profile.target_bg,
                    "targetHigh": profile.target_bg, "currentBasal": profile.current_basal,
                })
                candidate = auto.get("sens")
                if candidate is not None and math.isfinite(candidate) and candidate > 0:
                    variable_sens = float(candidate)

            meal_json = {"carbs": 0.0, "mealCOB": 0.0}
            if scenario.meal_mode == "announced":
                meal_result = bridge.request({
                    "cmd": "meal", "now": now_ms,
                    "readings": cgm_history, "sourceSensor": "Libre2",
                    "isf": profile.sens, "carbRatio": profile.carb_ratio,
                    "target": profile.target_bg, "targetLow": profile.target_bg,
                    "targetHigh": profile.target_bg, "currentBasal": profile.current_basal,
                })
                meal_json = {
                    "carbs": meal_result["carbs"], "mealCOB": meal_result["mealCOB"],
                    "slopeFromMaxDeviation": meal_result["slopeFromMaxDeviation"],
                    "slopeFromMinDeviation": meal_result["slopeFromMinDeviation"],
                    "usedMinCarbsImpact": meal_result["usedMinCarbsImpact"],
                    "lastBolusTime": meal_result["lastBolusTime"],
                    "lastCarbTime": meal_result["lastCarbTime"],
                }
            cob_now = float(meal_json["mealCOB"])

            rt = bridge.request({"cmd": "step", "input": {
                "now": now_ms,
                "glucoseStatus": glucose,
                "currentTemp": current_temp,
                "iob": iob,
                "autosens": {"ratio": 1.0},
                "meal": meal_json,
                "profile": {
                    "sens": profile.sens, "variable_sens": variable_sens,
                    "carb_ratio": profile.carb_ratio, "target_bg": profile.target_bg,
                    "min_bg": profile.target_bg, "max_bg": profile.target_bg,
                    "current_basal": profile.current_basal, "max_iob": profile.max_iob,
                    "max_basal": profile.max_basal, "SMBInterval": profile.smb_interval,
                    "bolus_increment": profile.bolus_increment,
                    "enable_autoISF": profile.enable_auto_isf,
                    "out_units": "mg/dl", "temptargetSet": False,
                },
                "microBolusAllowed": micro_bolus_allowed,
                "flatBGs": False,
                "autoIsfMode": profile.enable_auto_isf,
                "loopWantedSmb": "AAPS",
                "smbRatio": smb_ratio_for_cadence(profile, cadence_min),
                "smbMaxRangeExtension": 1.0,
                "iobThresholdPercent": 100,
                "profilePercentage": 100,
                "activityConsoleLog": "",
            }})["rt"]

            insulin_req = float(rt.get("insulinReq") or 0.0)
            reason_text = str(rt.get("reason") or "")
            rate = rt.get("rate")
            if planned_temps:
                if rate is not None and not suspended_now:
                    duration_min = rt.get("duration")
                    if duration_min is None or float(duration_min) <= 0.0:
                        if temp_rate is not None:
                            applied_temps.append({
                                "kind": "temp", "ts": now_ms,
                                "duration": int(cadence_min * 60_000),
                                "rate": basal_u_per_h, "isAbsolute": True, "isValid": True,
                            })
                        temp_rate = None
                    else:
                        temp_rate = pump.temp_basal(float(rate))
                        temp_start = t1
                        temp_end = t1 + float(duration_min)
                        applied_temps.append({
                            "kind": "temp", "ts": now_ms,
                            "duration": int(float(duration_min) * 60_000),
                            "rate": temp_rate, "isAbsolute": True, "isValid": True,
                        })
            elif rate is not None and rate >= 0:
                rate_now = pump.temp_basal(float(rate))

            units = 0.0 if suspended_now else float(rt.get("units") or 0.0)
            if units > 0:
                delivered = pump.bolus(units)
                if delivered > 0:
                    pending_boluses.append((t1 + pump.config.bolus_delay_min, delivered))
                applied_boluses.append({
                    "kind": "bolus", "ts": now_ms, "amount": delivered,
                    "type": "SMB", "isValid": True,
                })
                result.dose_events.append((t1, delivered, "bolus"))

        if planned_temps:
            if suspended_now:
                applied_temps.append({
                    "kind": "temp", "ts": EPOCH0_MS + int(t0 * 60_000),
                    "duration": int(cadence_min * 60_000),
                    "rate": 0.0, "isAbsolute": True, "isValid": True,
                })
            delivered_rate = segment_rate
        else:
            if suspended_now:
                rate_now = 0.0
            basal_u_per_h = rate_now
            applied_temps.append({
                "kind": "temp", "ts": now_ms, "duration": int(cadence_min * 60_000),
                "rate": rate_now, "isAbsolute": True, "isValid": True,
            })
            delivered_rate = rate_now

        basal_units = delivered_rate * cadence_min / 60.0
        result.dose_events.append((t1, basal_units, "basal"))

        result.time.append(t1)
        result.cgm.append(value)
        result.glucose.append(g_plasma)
        result.rate.append(delivered_rate)
        result.insulin_req.append(insulin_req)
        result.variable_sens.append(variable_sens)
        result.current_temp.append(dict(current_temp))
        result.iob.append(iob_now)
        result.cob.append(cob_now)
        result.reason.append(reason_text)

        if on_cycle is not None:
            on_cycle(result)

    return result
