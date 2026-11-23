"""Model mode: a virtual cohort subject, the user's real meals, boluses and profile."""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "python") not in sys.path:
    sys.path.insert(0, str(ROOT / "python"))

from nightscout.trace import DayTrace, trace_from_dict
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.metrics import compute_metrics
from runner.replay_loop import (
    ReplayCycle,
    ReplayVariant,
    _effective_profile,
    _pref,
    _pref_int,
    _total_daily_dose,
    _with_override,
)
from simcore.params import Subject, load_subjects
from simcore.population import cached_subject_profile

CADENCE_MIN = 5.0
DEFAULT_WARMUP_HOURS = 8.0
GOOD_DISTANCE = 0.35


@dataclass
class PatientMatch:
    subject_name: str
    distance: float
    subject_tdi: float
    subject_cr: float
    subject_isf: float
    user_tdi: float
    user_cr: float
    user_isf: float
    confidence: str


@dataclass
class ModelResult:
    match: PatientMatch
    cycles: list[ReplayCycle] = field(default_factory=list)
    glucose_offset: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _subject_values(subject) -> tuple[float, float, float] | None:
    values: list[float] = []
    for key in ("cr", "tdi", "isf"):
        value = getattr(subject, key, None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            values = []
            break
        values.append(float(value))
    if len(values) != 3:
        profile = cached_subject_profile(subject, "rule")
        numbers = [profile[key] for key in ("cr", "tdi", "isf")]
        if any(value is None for value in numbers):
            return None
        values = [float(value) for value in numbers]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        return None
    return values[0], values[1], values[2]


def _match_one(subject, name: str, tdi: float, cr: float, isf: float) -> PatientMatch | None:
    values = _subject_values(subject)
    if values is None:
        return None
    subject_cr, subject_tdi, subject_isf = values
    distance = math.sqrt(
        math.log(tdi / subject_tdi) ** 2
        + math.log(cr / subject_cr) ** 2
        + math.log(isf / subject_isf) ** 2
    )
    return PatientMatch(
        subject_name=name,
        distance=distance,
        subject_tdi=subject_tdi,
        subject_cr=subject_cr,
        subject_isf=subject_isf,
        user_tdi=tdi,
        user_cr=cr,
        user_isf=isf,
        confidence="good" if distance <= GOOD_DISTANCE else "low",
    )


def match_subject(
    subjects: dict[str, Subject], tdi: float, cr: float, isf: float
) -> PatientMatch:
    """Pick the cohort subject closest to the user in log TDI/CR/ISF space."""
    try:
        numbers = (float(tdi), float(cr), float(isf))
    except (TypeError, ValueError) as exc:
        raise ValueError("user tdi, cr and isf must be positive finite numbers") from exc
    for value, label in zip(numbers, ("tdi", "cr", "isf")):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"user {label} must be a positive finite number")
    tdi, cr, isf = numbers
    best: PatientMatch | None = None
    for name in sorted(subjects):
        subject = subjects[name]
        candidate = _match_one(subject, getattr(subject, "name", name), tdi, cr, isf)
        if candidate is None:
            continue
        if best is None or candidate.distance < best.distance:
            best = candidate
    if best is None:
        raise ValueError("no cohort subject has a usable TDI/CR/ISF profile")
    return best


def _scenario_minute(trace: DayTrace, ts: int, warmup: int) -> float:
    if ts < trace.window_start:
        return warmup - (trace.window_start - ts) / 60_000
    return warmup + (ts - trace.window_start) / 60_000


def trace_scenario(trace: DayTrace, warmup_hours: float = DEFAULT_WARMUP_HOURS) -> Scenario:
    """Real meals and manual boluses shifted onto a scenario clock with a warmup block."""
    warmup = int(round(warmup_hours * 60))
    since = trace.window_start - warmup * 60_000
    meals: list[tuple[float, float]] = []
    boluses: list[tuple[float, float]] = []
    for treatment in trace.treatments:
        if treatment.ts < since or treatment.ts > trace.window_end:
            continue
        if treatment.kind not in ("carbs", "bolus") or treatment.amount <= 0:
            continue
        if treatment.kind == "bolus" and treatment.origin == "loop":
            continue
        minute = _scenario_minute(trace, treatment.ts, warmup)
        if minute < 0:
            continue
        entry = (minute, float(treatment.amount))
        if treatment.kind == "carbs":
            meals.append(entry)
        else:
            boluses.append(entry)
    meals.sort()
    boluses.sort()
    duration = int(
        round(warmup + (trace.window_end - trace.window_start) / 60_000)
    )
    return Scenario(
        name="model",
        duration_min=duration,
        meals=meals,
        boluses=boluses,
        meal_mode="announced",
    )


def _real_glucose_at(trace: DayTrace, ts: int) -> float | None:
    before = [entry for entry in trace.entries if entry.ts <= ts]
    after = [entry for entry in trace.entries if entry.ts > ts]
    chosen = max(before, key=lambda entry: entry.ts) if before else (
        min(after, key=lambda entry: entry.ts) if after else None
    )
    return None if chosen is None else float(chosen.mgdl)


def _user_profile(
    trace: DayTrace, variant: ReplayVariant
) -> tuple[dict[str, float], float]:
    values = _effective_profile(
        trace.profile_at(trace.window_start), trace.window_start, variant.profile_overrides
    )
    tdd = _with_override(
        _total_daily_dose(trace, values["basal"]), variant.profile_overrides, "tdd"
    )
    return values, tdd


def build_algorithm_profile(
    values: dict[str, float], tdd: float, prefs: dict, variant: ReplayVariant
) -> AlgorithmProfile:
    """The user's own Nightscout profile, never the cohort subject's."""
    return AlgorithmProfile(
        sens=values["isf"],
        carb_ratio=values["carb_ratio"],
        target_bg=values["target"],
        max_iob=_pref(prefs, "openapsmb_max_iob", 12.0),
        max_basal=_pref(prefs, "openapsma_max_basal", 4.0),
        current_basal=values["basal"],
        smb_interval=_pref_int(prefs, "smbinterval", 3),
        bolus_increment=_pref(prefs, "bolus_increment", 0.05),
        enable_auto_isf=variant.algorithm == "autoisf",
        smb_ratio_3min=_pref(prefs, "openapsama_smb_delivery_ratio", 0.5),
        algorithm=variant.algorithm,
        tdd=tdd,
    )


def _cycles_from_loop(
    trace: DayTrace, loop, offset: float, warmup: int, cadence: float
) -> list[ReplayCycle]:
    start = int(round(warmup / cadence))
    smb: dict[float, float] = {}
    for time_min, units, kind in loop.dose_events:
        if kind == "bolus":
            key = round(float(time_min), 6)
            smb[key] = smb.get(key, 0.0) + float(units)
    cycles: list[ReplayCycle] = []
    for index in range(start, len(loop.time)):
        time_min = float(loop.time[index])
        cycles.append(ReplayCycle(
            ts=trace.window_start + int(round((time_min - warmup) * 60_000)),
            glucose=float(loop.cgm[index]) + offset,
            rate=float(loop.rate[index]),
            smb=smb.get(round(time_min, 6), 0.0),
            insulin_req=float(loop.insulin_req[index]),
            final_isf=float(loop.variable_sens[index]),
            factors={},
            iob=float(loop.iob[index]),
            eventual_bg=None,
            sensitivity_ratio=1.0,
            reason=str(loop.reason[index]),
        ))
    return cycles


def run_model(
    trace: DayTrace,
    prefs: dict,
    insulin: dict,
    variant: ReplayVariant,
    bridge,
    subject: Subject | None = None,
) -> ModelResult:
    """Simulate the user's real meals and boluses on a virtual cohort subject."""
    prefs_effective = {**prefs, **variant.prefs}
    scenario = trace_scenario(trace)
    warmup = int(round(DEFAULT_WARMUP_HOURS * 60))
    values, tdd = _user_profile(trace, variant)
    for key in ("isf", "carb_ratio", "basal", "target"):
        if values[key] <= 0:
            raise ValueError(f"trace profile has no usable {key} at window_start")
    if subject is None:
        subjects = load_subjects()
        match = match_subject(subjects, tdi=tdd, cr=values["carb_ratio"], isf=values["isf"])
        subject = subjects[match.subject_name]
    else:
        match = _match_one(
            subject, getattr(subject, "name", "subject"),
            tdi=tdd, cr=values["carb_ratio"], isf=values["isf"],
        )
        if match is None:
            raise ValueError("subject has no usable TDI/CR/ISF profile")
    profile = build_algorithm_profile(values, tdd, prefs_effective, variant)
    init_bridge(bridge, subject, prefs_effective, insulin, profile, scenario)
    loop = run_closed_loop(
        subject, bridge, profile, scenario,
        cadence_min=CADENCE_MIN, seed=0, noise_kind="none", on_cycle=None,
        micro_bolus_allowed=variant.smb,
    )
    start = int(round(warmup / CADENCE_MIN))
    warnings: list[str] = []
    real = _real_glucose_at(trace, trace.window_start)
    if real is None:
        offset = 0.0
        warnings.append("no cgm reading near window_start: glucose offset is 0")
    elif start <= 0 or start - 1 >= len(loop.cgm):
        offset = 0.0
        warnings.append("no model glucose at warmup: glucose offset is 0")
    else:
        offset = real - float(loop.cgm[start - 1])
    if match.confidence != "good":
        warnings.append(
            f"subject similarity is low: distance {match.distance:.2f} > {GOOD_DISTANCE}"
        )
    since = trace.window_start - warmup * 60_000
    excluded = sum(
        1 for treatment in trace.treatments
        if since <= treatment.ts <= trace.window_end
        and treatment.kind == "bolus" and treatment.origin == "loop"
    )
    if excluded:
        warnings.append(f"excluded loop boluses: {excluded}")
    warnings.extend(f"trace: {warning}" for warning in trace.warnings)
    return ModelResult(
        match=match,
        cycles=_cycles_from_loop(trace, loop, offset, warmup, CADENCE_MIN),
        glucose_offset=offset,
        warnings=warnings,
    )


def _metrics(values) -> dict:
    array = np.asarray(values, dtype=float)
    metrics = compute_metrics(array, [])
    return {
        "n": metrics.n,
        "mean_glucose": round(metrics.mean_glucose, 1),
        "tir_70_180": round(metrics.tir_70_180, 4),
        "tbr_70": round(metrics.tbr_70, 4),
        "tbr_54": round(metrics.tbr_54, 4),
        "lbgi": round(metrics.lbgi, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Model what-if run on a Nightscout trace.")
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--prefs", required=True, type=Path)
    parser.add_argument("--insulin", default="Lyumjev 45m 8h 100 ед")
    parser.add_argument("--algorithm", default="autoisf", choices=("off", "autoisf", "dynisf"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    from bridge.client import Bridge, insulin_from_settings, load_prefs

    trace = trace_from_dict(json.loads(args.trace.read_text(encoding="utf-8")))
    prefs = load_prefs(args.prefs)
    insulin = insulin_from_settings(args.prefs, args.insulin)
    variant = ReplayVariant(label="model", algorithm=args.algorithm, smb=True)
    with Bridge() as bridge:
        result = run_model(trace, prefs, insulin, variant, bridge)
    real = [
        entry.mgdl for entry in trace.entries
        if trace.window_start <= entry.ts <= trace.window_end
    ]
    model = [cycle.glucose for cycle in result.cycles]
    real_metrics = _metrics(real)
    model_metrics = _metrics(model)
    scenario = trace_scenario(trace)
    warmup = DEFAULT_WARMUP_HOURS * 60
    smb_units = sum(cycle.smb for cycle in result.cycles)
    basal_units = sum(cycle.rate * CADENCE_MIN / 60.0 for cycle in result.cycles)
    manual_units = sum(units for minute, units in scenario.boluses if minute >= warmup)
    rates = [cycle.rate for cycle in result.cycles]
    payload = {
        "match": asdict(result.match),
        "glucose_offset": round(result.glucose_offset, 2),
        "warnings": result.warnings,
        "window_hours": round(
            (trace.window_end - trace.window_start) / 3_600_000, 2
        ),
        "cycles": len(result.cycles),
        "scenario": {
            "meals": len(scenario.meals),
            "boluses": len(scenario.boluses),
        },
        "model": model_metrics,
        "real": real_metrics,
        "insulin": {
            "smb_units": round(smb_units, 2),
            "basal_units": round(basal_units, 2),
            "manual_units": round(manual_units, 2),
            "total_units": round(smb_units + basal_units + manual_units, 2),
            "rate_mean_u_per_h": round(float(np.mean(rates)), 2),
            "rate_min_u_per_h": round(float(np.min(rates)), 2),
            "rate_max_u_per_h": round(float(np.max(rates)), 2),
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
