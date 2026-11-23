#!/usr/bin/env python3
"""Normalize Nightscout documents into a replayable day trace."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MGDL_PER_MMOL = 18.0
CGM_GAP_MS = 15 * 60_000

_BOLUS_EVENTS = {"correction bolus", "meal bolus", "bolus"}


@dataclass
class CgmPoint:
    ts: int
    mgdl: float
    direction: str = "NONE"
    device: str = ""


@dataclass
class Treatment:
    ts: int
    kind: str
    amount: float = 0.0
    duration_min: float = 0.0
    origin: str = "manual"
    meta: dict = field(default_factory=dict)


@dataclass
class DecisionRecord:
    ts: int
    deliver_at: int
    insulin_req: float | None
    final_isf: float | None
    factors: dict[str, float]
    smb_on: bool
    rate: float | None
    eventual_bg: float | None
    sensitivity_ratio: float | None


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _as_ms(value: object) -> int | None:
    epoch = _as_int(value)
    if epoch is not None:
        return epoch
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return int(moment.timestamp() * 1000)


def _minute_of_day(ts: int, timezone: str) -> int:
    try:
        zone = ZoneInfo(timezone) if timezone else UTC
    except (ZoneInfoNotFoundError, ValueError):
        zone = UTC
    moment = datetime.fromtimestamp(ts / 1000, tz=zone)
    return moment.hour * 60 + moment.minute


def _curve(raw: object, factor: float) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    if not isinstance(raw, list):
        return points
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = _as_float(item.get("value"))
        minute = _minute_of_day_text(item.get("time"))
        if value is None or minute is None:
            continue
        points.append((minute, value * factor))
    points.sort()
    return points


def _minute_of_day_text(value: object) -> int | None:
    if value is None:
        return None
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    return hour * 60 + minute


@dataclass
class ProfileSegment:
    start_ms: int
    basal: list[tuple[int, float]] = field(default_factory=list)
    sens: list[tuple[int, float]] = field(default_factory=list)
    carb_ratio: list[tuple[int, float]] = field(default_factory=list)
    target_low: list[tuple[int, float]] = field(default_factory=list)
    target_high: list[tuple[int, float]] = field(default_factory=list)
    units: str = "mg/dL"
    timezone: str = "UTC"

    def _value_at(self, values: list[tuple[int, float]], ts: int) -> float:
        if not values:
            return 0.0
        minute = _minute_of_day(ts, self.timezone)
        selected = [value for start, value in values if start <= minute]
        return selected[-1] if selected else values[0][1]

    def basal_at(self, ts: int) -> float:
        """Return the scheduled basal rate (U/h) in effect at ts."""
        return self._value_at(self.basal, ts)

    def sens_at(self, ts: int) -> float:
        """Return the insulin sensitivity (mg/dL/U) in effect at ts."""
        return self._value_at(self.sens, ts)

    def carb_ratio_at(self, ts: int) -> float:
        """Return the carb ratio (g/U) in effect at ts."""
        return self._value_at(self.carb_ratio, ts)

    def target_low_at(self, ts: int) -> float:
        """Return the low target (mg/dL) in effect at ts."""
        return self._value_at(self.target_low, ts)

    def target_high_at(self, ts: int) -> float:
        """Return the high target (mg/dL) in effect at ts."""
        return self._value_at(self.target_high, ts)


@dataclass
class DayTrace:
    entries: list[CgmPoint] = field(default_factory=list)
    treatments: list[Treatment] = field(default_factory=list)
    profiles: list[ProfileSegment] = field(default_factory=list)
    decisions: list[DecisionRecord] = field(default_factory=list)
    window_start: int = 0
    window_end: int = 0
    timezone: str = "UTC"
    sensor: str = ""
    warnings: list[str] = field(default_factory=list)

    def profile_at(self, ts: int) -> ProfileSegment:
        """Return the profile segment active at ts."""
        if not self.profiles:
            return ProfileSegment(start_ms=0, timezone=self.timezone)
        active = self.profiles[0]
        for segment in self.profiles:
            if segment.start_ms > ts:
                break
            active = segment
        return active

    def active_temp_target(self, ts: int) -> tuple[float, float] | None:
        """Return the low/high target (mg/dL) of the last running temp target."""
        active: Treatment | None = None
        for treatment in self.treatments:
            if treatment.kind != "temp_target" or treatment.ts > ts:
                continue
            if "low" not in treatment.meta or "high" not in treatment.meta:
                continue
            if ts >= treatment.ts + treatment.duration_min * 60_000:
                continue
            if active is None or treatment.ts >= active.ts:
                active = treatment
        if active is None:
            return None
        return float(active.meta["low"]), float(active.meta["high"])

    def autosens_at(self, ts: int) -> float:
        """Return the sensitivity ratio from the last decision at or before ts."""
        chosen: DecisionRecord | None = None
        for decision in self.decisions:
            if decision.ts <= ts and (chosen is None or decision.ts >= chosen.ts):
                chosen = decision
        if chosen is None or chosen.sensitivity_ratio is None:
            return 1.0
        return chosen.sensitivity_ratio

    def temp_basal_at(self, ts: int) -> tuple[float, float] | None:
        """Return (rate, remaining minutes) of the last temp basal still running at ts."""
        chosen: Treatment | None = None
        for treatment in self.treatments:
            if treatment.kind != "temp_basal" or treatment.ts > ts:
                continue
            if chosen is None or treatment.ts >= chosen.ts:
                chosen = treatment
        if chosen is None:
            return None
        remaining = chosen.duration_min - (ts - chosen.ts) / 60_000
        if remaining <= 0:
            return None
        return chosen.amount, remaining


def _parse_entries(entries: list[dict], window_start: int, window_end: int) -> list[CgmPoint]:
    by_date: dict[int, CgmPoint] = {}
    for raw in entries:
        if not isinstance(raw, dict) or raw.get("type") != "sgv":
            continue
        ts = _as_int(raw.get("date"))
        mgdl = _as_float(raw.get("sgv"))
        if ts is None or mgdl is None or not window_start <= ts <= window_end:
            continue
        by_date[ts] = CgmPoint(
            ts=ts,
            mgdl=mgdl,
            direction=str(raw.get("direction") or "NONE"),
            device=str(raw.get("sensor") or raw.get("device") or ""),
        )
    return [by_date[ts] for ts in sorted(by_date)]


def _parse_treatments(
    raw_treatments: list[dict], segments: list[ProfileSegment]
) -> tuple[list[Treatment], list[str]]:
    parsed: list[Treatment] = []
    warnings: list[str] = []
    unclassified = 0
    for raw in raw_treatments:
        if not isinstance(raw, dict):
            continue
        ts = _as_ms(raw.get("date"))
        if ts is None:
            ts = _as_ms(raw.get("created_at"))
        if ts is None:
            continue
        event = str(raw.get("eventType") or "").strip().lower()
        amount = _as_float(raw.get("insulin")) or 0.0
        duration = _as_float(raw.get("duration")) or 0.0
        carbs = _as_float(raw.get("carbs")) or 0.0
        if event in _BOLUS_EVENTS:
            bolus_type = str(raw.get("type") or "").strip().upper()
            if bolus_type == "SMB":
                origin = "loop"
            elif bolus_type == "NORMAL":
                origin = "manual"
            else:
                origin = "unknown"
                unclassified += 1
            parsed.append(Treatment(ts=ts, kind="bolus", amount=amount, origin=origin))
            if carbs > 0:
                parsed.append(Treatment(ts=ts, kind="carbs", amount=carbs, origin="manual"))
        elif event == "carb correction":
            parsed.append(Treatment(ts=ts, kind="carbs", amount=carbs, origin="manual"))
        elif event == "temp basal":
            rate = _as_float(raw.get("absolute"))
            if rate is None:
                rate = _as_float(raw.get("rate")) or 0.0
            parsed.append(
                Treatment(
                    ts=ts,
                    kind="temp_basal",
                    amount=rate,
                    duration_min=duration,
                    origin="pump",
                )
            )
        elif event == "temporary target":
            bottom = _as_float(raw.get("targetBottom"))
            top = _as_float(raw.get("targetTop"))
            units = str(raw.get("units") or "").strip() or _units_at(segments, ts)
            factor = _unit_factor(units)
            if factor is None:
                factor = 1.0
                warnings.append(f"temp target units unknown: {units or 'missing'}")
            if bottom is None and top is None:
                meta: dict = {}
            else:
                low = bottom if bottom is not None else top
                high = top if top is not None else bottom
                meta = {"low": float(low) * factor, "high": float(high) * factor}
            parsed.append(
                Treatment(ts=ts, kind="temp_target", duration_min=duration, meta=meta)
            )
        elif event == "profile switch":
            profile = raw.get("profile")
            meta = {"profile": str(profile)} if profile else {}
            parsed.append(Treatment(ts=ts, kind="profile_switch", meta=meta))
        else:
            parsed.append(Treatment(ts=ts, kind="note", origin="manual"))
    parsed.sort(key=lambda treatment: treatment.ts)
    if unclassified:
        warnings.insert(0, f"unclassified bolus: {unclassified}")
    return parsed, warnings


def _units_at(segments: list[ProfileSegment], ts: int) -> str:
    if not segments:
        return ""
    active = segments[0]
    for segment in segments:
        if segment.start_ms > ts:
            break
        active = segment
    return active.units


def _unit_factor(units: str) -> float | None:
    normalized = units.strip().lower().replace("/", "")
    if normalized == "mmol":
        return MGDL_PER_MMOL
    if normalized == "mgdl":
        return 1.0
    return None


def _parse_profiles(docs: list[dict], timezone: str | None) -> list[ProfileSegment]:
    parsed: list[tuple[int, dict, str, str]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        start = _as_ms(doc.get("startDate"))
        if start is None:
            start = _as_ms(doc.get("created_at")) or 0
        store = doc.get("store")
        values: dict = {}
        if isinstance(store, dict):
            preferred = store.get(doc.get("defaultProfile"))
            if isinstance(preferred, dict):
                values = preferred
            else:
                values = next((item for item in store.values() if isinstance(item, dict)), {})
        units = str(values.get("units") or "mg/dL")
        zone = timezone or str(values.get("timezone") or "UTC")
        parsed.append((start, values, units, zone))
    parsed.sort(key=lambda item: item[0])
    segments: list[ProfileSegment] = []
    for start, values, units, zone in parsed:
        factor = MGDL_PER_MMOL if units.lower() == "mmol" else 1.0
        previous = segments[-1] if segments else None
        basal = _curve(values["basal"], 1.0) if "basal" in values else _inherited(previous, "basal")
        sens = _curve(values["sens"], factor) if "sens" in values else _inherited(previous, "sens")
        carb_ratio = (
            _curve(values["carbratio"], 1.0)
            if "carbratio" in values
            else _inherited(previous, "carb_ratio")
        )
        target_low = (
            _curve(values["target_low"], factor)
            if "target_low" in values
            else _inherited(previous, "target_low")
        )
        target_high = (
            _curve(values["target_high"], factor)
            if "target_high" in values
            else _inherited(previous, "target_high")
        )
        segments.append(
            ProfileSegment(
                start_ms=start,
                basal=basal,
                sens=sens,
                carb_ratio=carb_ratio,
                target_low=target_low,
                target_high=target_high,
                units=units,
                timezone=zone,
            )
        )
    return segments


def _inherited(previous: ProfileSegment | None, name: str) -> list[tuple[int, float]]:
    return list(getattr(previous, name)) if previous is not None else []


def _parse_decisions(devicestatus: list[dict]) -> list[DecisionRecord]:
    by_delivery: dict[int, DecisionRecord] = {}
    for doc in devicestatus:
        if not isinstance(doc, dict):
            continue
        openaps = doc.get("openaps")
        suggested = openaps.get("suggested") if isinstance(openaps, dict) else None
        if not isinstance(suggested, dict):
            continue
        deliver_at = _as_ms(suggested.get("deliverAt"))
        ts = _as_ms(doc.get("created_at")) or deliver_at
        if ts is None:
            continue
        if deliver_at is None:
            deliver_at = ts
        auto_isf = suggested.get("autoIsf")
        auto_isf = auto_isf if isinstance(auto_isf, dict) else {}
        factors = {
            key: value
            for key, raw_value in auto_isf.items()
            if key not in ("finalIsf", "smbOn")
            for value in [_as_float(raw_value)]
            if value is not None
        }
        by_delivery[deliver_at] = DecisionRecord(
            ts=ts,
            deliver_at=deliver_at,
            insulin_req=_as_float(suggested.get("insulinReq")),
            final_isf=_as_float(auto_isf.get("finalIsf")),
            factors=factors,
            smb_on=bool(auto_isf.get("smbOn", False)),
            rate=_as_float(suggested.get("rate")),
            eventual_bg=_as_float(suggested.get("eventualBG")),
            sensitivity_ratio=_as_float(suggested.get("sensitivityRatio")),
        )
    return [by_delivery[deliver_at] for deliver_at in sorted(by_delivery)]


def _sensor_name(entries: list[CgmPoint]) -> str:
    names = [entry.device for entry in entries if entry.device]
    if not names:
        return ""
    return Counter(names).most_common(1)[0][0]


def build_trace(
    *,
    entries: list[dict],
    treatments: list[dict],
    profiles: list[dict],
    devicestatus: list[dict],
    window_start: int,
    window_end: int,
    timezone: str | None = None,
    history_start: int | None = None,
) -> DayTrace:
    """Normalize raw Nightscout documents into a DayTrace for the import window.

    CGM points are kept from history_start (default window_start) so replay gets a sensor
    window and seed history; treatments and decisions are never clipped.
    """
    warnings: list[str] = []
    start = window_start if history_start is None else history_start
    points = _parse_entries(entries, start, window_end)
    segments = _parse_profiles(profiles, timezone)
    parsed_treatments, treatment_warnings = _parse_treatments(treatments, segments)
    warnings.extend(treatment_warnings)
    if not segments:
        warnings.append("no profile")
    if not devicestatus:
        warnings.append("no devicestatus: parity check disabled")
    decisions = _parse_decisions(devicestatus)
    gaps = sum(1 for left, right in zip(points, points[1:]) if right.ts - left.ts > CGM_GAP_MS)
    if gaps:
        warnings.append(f"cgm gaps > 15 min: {gaps}")
    zone = timezone or (segments[0].timezone if segments else "UTC")
    return DayTrace(
        entries=points,
        treatments=parsed_treatments,
        profiles=segments,
        decisions=decisions,
        window_start=window_start,
        window_end=window_end,
        timezone=zone,
        sensor=_sensor_name(points),
        warnings=warnings,
    )


def trace_to_dict(trace: DayTrace) -> dict:
    """Serialize a day trace to a JSON-ready dict."""
    return asdict(trace)


def _curve_from_dict(raw: object) -> list[tuple[int, float]]:
    if not isinstance(raw, list):
        return []
    points: list[tuple[int, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        points.append((int(item[0]), float(item[1])))
    return points


def trace_from_dict(data: dict) -> DayTrace:
    """Rebuild a day trace from trace_to_dict output."""
    entries = [
        CgmPoint(
            ts=int(item["ts"]),
            mgdl=float(item["mgdl"]),
            direction=str(item.get("direction", "NONE")),
            device=str(item.get("device", "")),
        )
        for item in data.get("entries", [])
    ]
    treatments = [
        Treatment(
            ts=int(item["ts"]),
            kind=str(item["kind"]),
            amount=float(item.get("amount", 0.0)),
            duration_min=float(item.get("duration_min", 0.0)),
            origin=str(item.get("origin", "manual")),
            meta=dict(item.get("meta", {})),
        )
        for item in data.get("treatments", [])
    ]
    profiles = [
        ProfileSegment(
            start_ms=int(item["start_ms"]),
            basal=_curve_from_dict(item.get("basal")),
            sens=_curve_from_dict(item.get("sens")),
            carb_ratio=_curve_from_dict(item.get("carb_ratio")),
            target_low=_curve_from_dict(item.get("target_low")),
            target_high=_curve_from_dict(item.get("target_high")),
            units=str(item.get("units", "mg/dL")),
            timezone=str(item.get("timezone", "UTC")),
        )
        for item in data.get("profiles", [])
    ]
    decisions = [
        DecisionRecord(
            ts=int(item["ts"]),
            deliver_at=int(item["deliver_at"]),
            insulin_req=item.get("insulin_req"),
            final_isf=item.get("final_isf"),
            factors={
                str(key): float(value)
                for key, value in (item.get("factors") or {}).items()
            },
            smb_on=bool(item.get("smb_on", False)),
            rate=item.get("rate"),
            eventual_bg=item.get("eventual_bg"),
            sensitivity_ratio=item.get("sensitivity_ratio"),
        )
        for item in data.get("decisions", [])
    ]
    return DayTrace(
        entries=entries,
        treatments=treatments,
        profiles=profiles,
        decisions=decisions,
        window_start=int(data.get("window_start", 0)),
        window_end=int(data.get("window_end", 0)),
        timezone=str(data.get("timezone", "UTC")),
        sensor=str(data.get("sensor", "")),
        warnings=list(data.get("warnings", [])),
    )
