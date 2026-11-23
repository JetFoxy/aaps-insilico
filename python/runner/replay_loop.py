#!/usr/bin/env python3
"""Counterfactual decisions on a real CGM trace; glucose is never recomputed."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CGM_GAP_MS = 15 * 60_000


@dataclass
class ReplayVariant:
    label: str
    algorithm: str = "autoisf"
    smb: bool = True
    prefs: dict = field(default_factory=dict)
    profile_overrides: dict = field(default_factory=dict)


@dataclass
class ReplayCycle:
    ts: int
    glucose: float
    rate: float | None
    smb: float
    insulin_req: float | None
    final_isf: float | None
    factors: dict[str, float]
    iob: float | None
    eventual_bg: float | None
    sensitivity_ratio: float | None
    reason: str


@dataclass
class ReplayResult:
    variant: ReplayVariant
    cycles: list[ReplayCycle] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _pref(prefs: dict, key: str, default: float) -> float:
    value = _float(prefs.get(key))
    return default if value is None else value


def _pref_int(prefs: dict, key: str, default: int) -> int:
    return int(round(_pref(prefs, key, float(default))))


def _override(overrides: dict, *keys: str) -> float | None:
    for key in keys:
        if key in overrides:
            value = _float(overrides[key])
            if value is not None:
                return value
    return None


def _with_override(value: float, overrides: dict, *keys: str) -> float:
    override = _override(overrides, *keys)
    return value if override is None else override


def _effective_profile(profile, now: int, overrides: dict) -> dict[str, float]:
    """Return the profile values at now with variant overrides applied."""
    values = {
        "basal": _with_override(profile.basal_at(now), overrides, "basal"),
        "isf": _with_override(profile.sens_at(now), overrides, "isf"),
        "carb_ratio": _with_override(
            profile.carb_ratio_at(now), overrides, "carb_ratio", "carbRatio"
        ),
        "target_low": _with_override(
            profile.target_low_at(now), overrides, "target_low", "targetLow"
        ),
        "target_high": _with_override(
            profile.target_high_at(now), overrides, "target_high", "targetHigh"
        ),
    }
    values["target"] = _with_override(values["target_low"], overrides, "target")
    return values


def _real_treatment(treatment) -> dict | None:
    """Bridge-format manual bolus or carbs; loop SMBs and other kinds are dropped."""
    if treatment.kind == "bolus":
        if treatment.origin == "loop":
            return None
        return {
            "kind": "bolus", "ts": treatment.ts, "amount": treatment.amount,
            "type": "NORMAL", "isValid": True,
        }
    if treatment.kind == "carbs":
        if treatment.amount <= 0:
            return None
        return {
            "kind": "carbs", "ts": treatment.ts,
            "amount": treatment.amount, "duration": 0,
        }
    return None


def _seed_history(trace, since_ms: int) -> list[dict]:
    """Bridge-format treatments from the seed window; SMB boluses never come back."""
    seeded: list[dict] = []
    for treatment in trace.treatments:
        if treatment.ts < since_ms or treatment.ts >= trace.window_start:
            continue
        if treatment.kind == "temp_basal":
            seeded.append({
                "kind": "temp", "ts": treatment.ts,
                "duration": int(round(treatment.duration_min * 60_000)),
                "rate": treatment.amount, "isAbsolute": True, "isValid": True,
            })
            continue
        converted = _real_treatment(treatment)
        if converted is not None:
            seeded.append(converted)
    return seeded


def _temp_target(trace, now: int) -> dict | None:
    active = trace.active_temp_target(now)
    if active is None:
        return None
    low, high = active
    running = None
    for treatment in trace.treatments:
        if treatment.kind != "temp_target" or treatment.ts > now:
            continue
        if "low" not in treatment.meta or "high" not in treatment.meta:
            continue
        if now >= treatment.ts + treatment.duration_min * 60_000:
            continue
        if running is None or treatment.ts >= running.ts:
            running = treatment
    remaining = 0 if running is None else max(
        0, int(running.ts + running.duration_min * 60_000 - now)
    )
    return {"ts": now, "reason": "Custom", "high": float(high), "low": float(low),
            "duration": remaining}


def _current_temp(journal: list[dict], now: int) -> dict:
    chosen = None
    for treatment in journal:
        if treatment.get("kind") != "temp" or treatment["ts"] > now:
            continue
        if chosen is None or treatment["ts"] >= chosen["ts"]:
            chosen = treatment
    if chosen is None:
        return {"duration": 0, "rate": 0.0, "minutesrunning": 0}
    remaining_ms = chosen["duration"] - (now - chosen["ts"])
    if remaining_ms <= 0:
        return {"duration": 0, "rate": 0.0, "minutesrunning": 0}
    return {
        "duration": int(math.ceil(remaining_ms / 60_000)),
        "rate": float(chosen["rate"]),
        "minutesrunning": int((now - chosen["ts"]) / 60_000),
    }


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone) if timezone else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _local_date(ts: int, timezone: str) -> date:
    return datetime.fromtimestamp(ts / 1000, tz=_zone(timezone)).date()


def _local_minute_ms(day: date, minute: int, timezone: str) -> int:
    zone = _zone(timezone)
    days, remainder = divmod(minute, 1440)
    midnight = datetime.combine(
        day + timedelta(days=days), time(0, 0, tzinfo=zone)
    )
    return int((midnight + timedelta(minutes=remainder)).timestamp() * 1000)


def _basal_schedule(trace, basal_at_start: float) -> list[list[int | float]]:
    """Bridge basal timeline: the window-start baseline plus profile breakpoints."""
    blocks: dict[int, float] = {trace.window_start: basal_at_start}
    for index, segment in enumerate(trace.profiles):
        if not segment.basal:
            continue
        active_end = (
            trace.profiles[index + 1].start_ms
            if index + 1 < len(trace.profiles)
            else None
        )
        day = _local_date(trace.window_start, segment.timezone)
        last_day = _local_date(trace.window_end, segment.timezone)
        while day <= last_day:
            for minute, rate in segment.basal:
                ts = _local_minute_ms(day, minute, segment.timezone)
                if ts < segment.start_ms:
                    continue
                if active_end is not None and ts >= active_end:
                    continue
                blocks[ts] = rate
            day += timedelta(days=1)
    return [[ts, blocks[ts]] for ts in sorted(blocks)]


def _total_daily_dose(trace, basal: float) -> float:
    hours = (trace.window_end - trace.window_start) / 3_600_000
    manual = sum(
        treatment.amount for treatment in trace.treatments
        if treatment.kind == "bolus" and treatment.origin != "loop"
        and trace.window_start <= treatment.ts <= trace.window_end
    )
    if hours <= 0:
        return basal * 24
    return basal * 24 + manual * 24 / hours


def _iob_from(response: dict) -> float | None:
    iob = response.get("iob")
    rows = iob.get("iob") if isinstance(iob, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    first = rows[0]
    return _float(first.get("iob")) if isinstance(first, dict) else None


def _bridge_algorithm(name: str) -> str | None:
    return name if name in ("autoisf", "dynisf") else None


def run_replay(
    trace,
    prefs: dict,
    insulin: dict,
    variant: ReplayVariant,
    bridge,
    *,
    cadence_min: float = 5.0,
    readings_window_min: int = 180,
    seed_history_hours: float = 8.0,
) -> ReplayResult:
    """Replay the day's raw CGM through the bridge and record only its decisions.

    Glucose is the real sensor value on every cycle, so variants differ only in the
    insulin they delivered. The returned cycles are decisions, not a glucose forecast.
    """
    prefs_effective = {**prefs, **variant.prefs}
    algorithm = _bridge_algorithm(variant.algorithm)
    auto_isf_mode = variant.algorithm == "autoisf"
    seed_since = trace.window_start - int(seed_history_hours * 3_600_000)
    seeded = _seed_history(trace, seed_since)
    start_values = _effective_profile(
        trace.profile_at(trace.window_start), trace.window_start, variant.profile_overrides
    )
    bridge.request({
        "cmd": "init",
        "prefs": prefs_effective,
        "profiles": [],
        "insulin": insulin,
        "treatments": seeded,
        "basal": _basal_schedule(trace, start_values["basal"]),
        "timezone": trace.timezone,
    })
    journal = list(seeded)
    result = ReplayResult(variant=variant)
    in_window = sorted(
        (
            treatment for treatment in trace.treatments
            if trace.window_start <= treatment.ts <= trace.window_end
            and treatment.kind in ("bolus", "carbs")
        ),
        key=lambda treatment: treatment.ts,
    )
    excluded_loop = sum(
        1 for treatment in in_window
        if treatment.kind == "bolus" and treatment.origin == "loop"
    )
    unclassified = sum(
        1 for treatment in in_window
        if treatment.kind == "bolus" and treatment.origin == "unknown"
    )
    if excluded_loop:
        result.warnings.append(f"excluded loop boluses: {excluded_loop}")
    if unclassified:
        result.warnings.append(f"unclassified boluses: {unclassified}")
    pending = 0
    step_ms = int(cadence_min * 60_000)
    if step_ms <= 0:
        raise ValueError("cadence_min must be positive")

    now = trace.window_start
    while now <= trace.window_end:
        while pending < len(in_window) and in_window[pending].ts <= now:
            converted = _real_treatment(in_window[pending])
            if converted is not None:
                journal.append(converted)
            pending += 1
        window_lo = now - int(readings_window_min * 60_000)
        readings = [[entry.ts, entry.mgdl] for entry in trace.entries
                    if window_lo <= entry.ts <= now]
        if not readings or readings[-1][0] < now - CGM_GAP_MS:
            result.skipped.append(now)
            now += step_ms
            continue
        readings.reverse()

        values = _effective_profile(
            trace.profile_at(now), now, variant.profile_overrides
        )
        ratio = trace.autosens_at(now)
        temp_target = _temp_target(trace, now)
        temptarget_set = temp_target is not None
        tdd = _with_override(
            _total_daily_dose(trace, values["basal"]), variant.profile_overrides, "tdd"
        )
        response = bridge.request({
            "cmd": "cycle",
            "now": now,
            "readings": readings,
            "sourceSensor": trace.sensor or "Libre2",
            "autosens": ratio,
            "exerciseMode": False,
            "halfBasalExerciseTarget": 160,
            "isTempTarget": temptarget_set,
            "tempTarget": temp_target,
            "treatments": list(journal),
            "mealMode": "announced",
            "isf": values["isf"],
            "carbRatio": values["carb_ratio"],
            "target": values["target"],
            "targetLow": values["target_low"],
            "targetHigh": values["target_high"],
            "currentBasal": values["basal"],
            "tdd": tdd,
            "algorithm": algorithm,
            "input": {
                "now": now,
                "TDD": tdd,
                "currentTemp": _current_temp(journal, now),
                "autosens": {"ratio": ratio},
                "profile": {
                    "sens": values["isf"],
                    "variable_sens": values["isf"],
                    "carb_ratio": values["carb_ratio"],
                    "target_bg": values["target"],
                    "min_bg": values["target_low"],
                    "max_bg": values["target_high"],
                    "current_basal": values["basal"],
                    "max_iob": _pref(prefs_effective, "openapsmb_max_iob", 12.0),
                    "max_basal": _pref(prefs_effective, "openapsma_max_basal", 4.0),
                    "SMBInterval": _pref_int(prefs_effective, "smbinterval", 3),
                    "bolus_increment": _pref(prefs_effective, "bolus_increment", 0.05),
                    "enable_autoISF": auto_isf_mode,
                    "out_units": "mg/dl",
                    "temptargetSet": temptarget_set,
                    "TDD": tdd,
                    "insulinDivisor": 75,
                    "use_TDD_for_predictions": True,
                },
                "microBolusAllowed": variant.smb,
                "flatBGs": False,
                "autoIsfMode": auto_isf_mode,
                "loopWantedSmb": "AAPS",
                "smbRatio": _pref(
                    prefs_effective, "openapsama_smb_delivery_ratio", 0.5
                ),
                "smbMaxRangeExtension": 1.0,
                "iobThresholdPercent": 100,
                "profilePercentage": 100,
                "activityConsoleLog": "",
            },
        })

        rt = response.get("rt")
        if not isinstance(rt, dict):
            result.warnings.append(f"cycle at {now} returned no decision")
            now += step_ms
            continue
        smb = _float(rt.get("units")) or 0.0
        rate = _float(rt.get("rate"))
        algorithm_response = response.get("algorithm")
        if not isinstance(algorithm_response, dict):
            algorithm_response = {}
        factors: dict[str, float] = {}
        raw_factors = algorithm_response.get("aiv")
        if isinstance(raw_factors, dict):
            for key, value in raw_factors.items():
                number = _float(value)
                if number is not None:
                    factors[str(key)] = number
        result.cycles.append(ReplayCycle(
            ts=now,
            glucose=float(readings[0][1]),
            rate=rate,
            smb=smb,
            insulin_req=_float(rt.get("insulinReq")),
            final_isf=_float(algorithm_response.get("sens")),
            factors=factors,
            iob=_iob_from(response),
            eventual_bg=_float(rt.get("eventualBG")),
            sensitivity_ratio=_float(rt.get("sensitivityRatio")),
            reason=str(rt.get("reason") or ""),
        ))
        if smb > 0:
            journal.append({
                "kind": "bolus", "ts": now, "amount": smb,
                "type": "SMB", "isValid": True,
            })
        if rate is not None:
            duration_min = _float(rt.get("duration"))
            if duration_min is None or duration_min <= 0:
                duration_min = cadence_min
            journal.append({
                "kind": "temp", "ts": now,
                "duration": int(duration_min * 60_000),
                "rate": rate, "isAbsolute": True, "isValid": True,
            })
        now += step_ms

    if result.skipped:
        result.warnings.append(f"cgm gap: {len(result.skipped)} cycles skipped")
    return result


def replay_to_dict(result: ReplayResult) -> dict:
    """Serialize a replay result to a JSON-ready dict."""
    return asdict(result)


def replay_from_dict(data: dict) -> ReplayResult:
    """Rebuild a replay result from replay_to_dict output."""
    return ReplayResult(
        variant=ReplayVariant(**data["variant"]),
        cycles=[ReplayCycle(**cycle) for cycle in data.get("cycles", [])],
        skipped=[int(ts) for ts in data.get("skipped", [])],
        warnings=list(data.get("warnings", [])),
    )
