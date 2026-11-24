#!/usr/bin/env python3
"""Live demo server: runs the closed loop and streams cycles over SSE (stdlib only)."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import queue
import sys
import threading
import time
import urllib.parse
import uuid
import zlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from bridge.client import Bridge, insulin_from_settings, load_prefs
from nightscout.client import NightscoutClient, NightscoutError, truncation_warnings
from nightscout.prefs import PrefsDecryptError, decode_export
from nightscout.trace import DayTrace, build_trace
from runner.closed_loop import init_bridge, run_closed_loop
from runner.export_demo import build_scenario, profile_for
from runner.metrics import compute_metrics
from runner.options import add_options, apply_prefs, load_cohort, noise_kwargs
from runner.replay_loop import ReplayVariant, run_replay
from runner.whatif_model import run_model
from simcore.params import load_subjects
from simcore.variability import si_day

WEB = ROOT / "web"
BASE_PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
RESIDUALS = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")

SCENARIO_LABELS = {
    "fcl-24h": "FCL 24 ч",
    "fcl-exercise": "FCL + нагрузка",
    "announced-8h": "объявленная еда 8 ч",
    "fault-delay": "задержка болюса 15 мин",
    "cgm-bias": "смещение CGM +50",
}
NOISE_CHOICES = ("residuals", "ar1", "white", "none")
UI_PREF_KEYS = (
    "bgAccel_ISF_weight",
    "bgBrake_ISF_weight",
    "lower_ISFrange_weight",
    "higher_ISFrange_weight",
    "pp_ISF_weight",
    "dura_ISF_weight",
    "autoISF_min",
    "autoISF_max",
    "dynisf_velocity",
    "dynisf_bg_cap",
)

STREAMS: dict[str, "queue.Queue[dict]"] = {}
DEFAULTS: dict = {}
DEFAULT_PREFS: dict = {}

WHATIF_DAY_MS = 86_400_000
WHATIF_LOOKBACK_MS = 8 * 3_600_000
WHATIF_DEFAULT_DAYS = 3
WHATIF_MAX_DAYS = 14
WHATIF_MAX_VARIANTS = 4
WHATIF_TTL_S = 2 * 3600
WHATIF_MODES = ("replay", "model")
WHATIF_ALGORITHMS = ("off", "autoisf", "dynisf")

TRACES: dict[str, DayTrace] = {}
WHATIF_SETTINGS: dict[str, dict] = {}
WHATIF_STREAMS: dict[str, "queue.Queue[dict]"] = {}
WHATIF_RUN_TRACE: dict[str, str] = {}
WHATIF_LOCK = threading.Lock()


class WhatifError(RuntimeError):
    """A what-if request failure carrying the HTTP status to report."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _scrub(text: object, *secrets: object) -> str:
    redacted = str(text)
    for secret in secrets:
        if secret:
            redacted = redacted.replace(str(secret), "***")
    return redacted


def _num(value, fallback: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number):
        return fallback
    return min(high, max(low, number))


def resolve_params(payload: dict) -> tuple[dict, dict]:
    incoming = payload.get("params") or {}
    if not isinstance(incoming, dict):
        raise ValueError("params must be an object")
    noise = str(incoming.get("noise", DEFAULTS["noise"]))
    if noise not in NOISE_CHOICES:
        raise ValueError(f"unknown noise {noise!r}")
    params = {
        "cohort_copies": int(_num(incoming.get("cohort_copies"), DEFAULTS["cohort_copies"], 0, 5)),
        "cohort_seed": int(_num(incoming.get("cohort_seed"), DEFAULTS["cohort_seed"], 0, 2**31 - 1)),
        "noise": noise,
        "noise_scale": _num(incoming.get("noise_scale"), DEFAULTS["noise_scale"], 0.0, 5.0),
        "noise_sigma": _num(incoming.get("noise_sigma"), DEFAULTS["noise_sigma"], 0.0, 50.0),
        "noise_ar1": _num(incoming.get("noise_ar1"), DEFAULTS["noise_ar1"], 0.0, 0.999),
        "isf_source": str(incoming.get("isf_source", DEFAULTS["isf_source"])),
        "temp_mode": str(incoming.get("temp_mode", DEFAULTS["temp_mode"])),
        "variability": bool(incoming.get("variability", DEFAULTS["variability"])),
        "smb_ratio_3min": _num(incoming.get("smb_ratio_3min"), DEFAULTS["smb_ratio_3min"], 0.05, 1.0),
    }
    if params["isf_source"] not in ("rule", "model"):
        raise ValueError("isf_source must be rule or model")
    if params["temp_mode"] not in ("planned", "cycle"):
        raise ValueError("temp_mode must be planned or cycle")

    overrides = incoming.get("prefs") or {}
    if not isinstance(overrides, dict):
        raise ValueError("prefs must be an object")
    prefs = dict(BASE_PREFS)
    prefs.update(DEFAULT_PREFS)
    prefs.update(overrides)
    return params, prefs


def _whatif_summary(trace: DayTrace, prefs_loaded: bool) -> dict:
    return {
        "entries": len(trace.entries),
        "treatments": len(trace.treatments),
        "decisions": len(trace.decisions),
        "profiles": len(trace.profiles),
        "window_start": trace.window_start,
        "window_end": trace.window_end,
        "sensor": trace.sensor,
        "prefs_loaded": prefs_loaded,
        "warnings": list(trace.warnings),
    }


def _insulin_from_configuration(decoded: dict) -> dict:
    raw = decoded.get("insulin_configuration")
    if not isinstance(raw, str):
        return dict(INSULIN)
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return dict(INSULIN)
    items = config.get("insulin") if isinstance(config, dict) else None
    if not isinstance(items, list):
        return dict(INSULIN)
    known = [item for item in items if isinstance(item, dict)]
    chosen = next(
        (item for item in known if item.get("insulinLabel") == INSULIN["label"]),
        known[0] if known else None,
    )
    if chosen is None:
        return dict(INSULIN)
    return {
        "label": str(chosen.get("insulinLabel") or INSULIN["label"]),
        "endTime": chosen.get("insulinEndTime", INSULIN["endTime"]),
        "peakTime": chosen.get("insulinPeakTime", INSULIN["peakTime"]),
        "concentration": chosen.get("concentration", INSULIN["concentration"]),
    }


def _whatif_settings(payload: dict) -> tuple[dict, dict, bool]:
    text = payload.get("prefs_text")
    if not isinstance(text, str) or not text.strip():
        prefs = dict(BASE_PREFS)
        prefs.update(DEFAULT_PREFS)
        return prefs, dict(INSULIN), False
    password = payload.get("password")
    decoded = decode_export(text, None if password in (None, "") else str(password))
    prefs = {
        key: value for key, value in decoded.items() if key != "insulin_configuration"
    }
    return prefs, _insulin_from_configuration(decoded), bool(prefs)


def _prune_whatif(now: float) -> None:
    with WHATIF_LOCK:
        expired = [
            trace_id
            for trace_id, settings in WHATIF_SETTINGS.items()
            if now - float(settings.get("created", now)) > WHATIF_TTL_S
        ]
        for trace_id in expired:
            _drop_whatif(trace_id)


def _drop_whatif(trace_id: str) -> None:
    """Remove an import and its runs; the caller holds WHATIF_LOCK."""
    TRACES.pop(trace_id, None)
    WHATIF_SETTINGS.pop(trace_id, None)
    for run_id, owner in list(WHATIF_RUN_TRACE.items()):
        if owner != trace_id:
            continue
        channel = WHATIF_STREAMS.pop(run_id, None)
        WHATIF_RUN_TRACE.pop(run_id, None)
        if channel is not None:
            channel.put({"type": "error", "message": "import deleted"})


def whatif_fetch(payload: dict) -> tuple[dict, str]:
    """Import a Nightscout window into memory; the token and password stay local."""
    url = payload.get("url")
    token = payload.get("token")
    token = None if token in (None, "") else str(token)
    api_secret = payload.get("api_secret")
    api_secret = None if api_secret in (None, "") else str(api_secret)
    password = payload.get("password")
    password = None if password in (None, "") else str(password)
    if not isinstance(url, str) or not url.strip():
        raise WhatifError("url is required", 400)
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise WhatifError("url must be an http(s) Nightscout address", 400)
    requested = int(_num(payload.get("days"), float(WHATIF_DEFAULT_DAYS), 1, 10**6))
    days = min(requested, WHATIF_MAX_DAYS)
    secrets = (url, token, api_secret, password, parts.netloc)
    source = hashlib.sha1(parts.netloc.encode()).hexdigest()[:12]
    try:
        prefs, insulin, prefs_loaded = _whatif_settings(payload)
        window_end = int(time.time() * 1000)
        window_start = window_end - days * WHATIF_DAY_MS
        history_start = window_start - WHATIF_LOOKBACK_MS
        client = NightscoutClient(url, token=token, api_secret=api_secret)
        client.status()
        profiles = client.profile()
        entries = client.entries(history_start, window_end)
        treatments = client.treatments(history_start, window_end)
        devicestatus = client.devicestatus(history_start, window_end)
        trace = build_trace(
            entries=entries,
            treatments=treatments,
            profiles=profiles,
            devicestatus=devicestatus,
            window_start=window_start,
            window_end=window_end,
            history_start=history_start,
        )
        trace.warnings.extend(truncation_warnings(entries, treatments, devicestatus))
        if days < requested:
            trace.warnings.append(f"window capped at {WHATIF_MAX_DAYS} days")
        if not trace.profiles:
            raise WhatifError("no profile in Nightscout", 409)
        if not any(window_start <= entry.ts <= window_end for entry in trace.entries):
            raise WhatifError("no cgm data in the import window", 409)
    except WhatifError:
        raise
    except NightscoutError as exc:
        status = 401 if exc.status in (401, 403) else 502
        raise WhatifError(_scrub(exc, *secrets), status) from None
    except PrefsDecryptError as exc:
        raise WhatifError(_scrub(exc, *secrets), 400) from None
    except Exception as exc:
        raise WhatifError(_scrub(f"{type(exc).__name__}: {exc}", *secrets), 500) from None

    trace_id = uuid.uuid4().hex[:12]
    now = time.time()
    _prune_whatif(now)
    with WHATIF_LOCK:
        for existing, settings in list(WHATIF_SETTINGS.items()):
            if settings.get("source") == source:
                _drop_whatif(existing)
        TRACES[trace_id] = trace
        WHATIF_SETTINGS[trace_id] = {
            "prefs": prefs,
            "insulin": insulin,
            "prefs_loaded": prefs_loaded,
            "source": source,
            "created": now,
        }
    return _whatif_summary(trace, prefs_loaded), trace_id


def _whatif_variants(raw: object) -> list[ReplayVariant]:
    if raw is None:
        raw = [{}]
    if not isinstance(raw, list) or not raw:
        raise WhatifError("variants must be a non-empty list", 400)
    if len(raw) > WHATIF_MAX_VARIANTS:
        raise WhatifError(f"at most {WHATIF_MAX_VARIANTS} variants", 400)
    variants: list[ReplayVariant] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise WhatifError("each variant must be an object", 400)
        label = str(item.get("label") or f"variant {index + 1}")
        algorithm = str(item.get("algorithm") or "autoisf")
        if algorithm not in WHATIF_ALGORITHMS:
            raise WhatifError(f"unknown algorithm {algorithm!r}", 400)
        smb = item.get("smb", True)
        if not isinstance(smb, bool):
            raise WhatifError("variant smb must be a boolean", 400)
        prefs = item.get("prefs") or {}
        if not isinstance(prefs, dict):
            raise WhatifError("variant prefs must be an object", 400)
        overrides = item.get("profile_overrides") or {}
        if not isinstance(overrides, dict):
            raise WhatifError("variant profile_overrides must be an object", 400)
        variants.append(ReplayVariant(
            label=label,
            algorithm=algorithm,
            smb=smb,
            prefs=dict(prefs),
            profile_overrides=dict(overrides),
        ))
    return variants


def _whatif_run_spec(payload: dict) -> dict:
    trace_id = payload.get("trace_id")
    if not isinstance(trace_id, str) or not trace_id:
        raise WhatifError("trace_id is required", 400)
    _prune_whatif(time.time())
    with WHATIF_LOCK:
        trace = TRACES.get(trace_id)
        settings = dict(WHATIF_SETTINGS.get(trace_id) or {})
    if trace is None:
        raise WhatifError("unknown trace", 404)
    mode = str(payload.get("mode") or "replay")
    if mode not in WHATIF_MODES:
        raise WhatifError(f"unknown mode {mode!r}", 400)
    if mode == "replay" and not settings.get("prefs_loaded"):
        raise WhatifError("replay requires the AAPS settings export", 400)
    options = payload.get("options") or {}
    if not isinstance(options, dict):
        raise WhatifError("options must be an object", 400)
    overrides = options.get("prefs") or {}
    if not isinstance(overrides, dict):
        raise WhatifError("options prefs must be an object", 400)
    subject_name = options.get("subject")
    subject = None
    if subject_name:
        subjects = load_subjects()
        if subject_name not in subjects:
            raise WhatifError(f"unknown subject {subject_name!r}", 400)
        subject = subjects[subject_name]
    return {
        "trace_id": trace_id,
        "mode": mode,
        "trace": trace,
        "prefs": {**(settings.get("prefs") or {}), **overrides},
        "insulin": settings.get("insulin") or dict(INSULIN),
        "prefs_loaded": bool(settings.get("prefs_loaded")),
        "variants": _whatif_variants(payload.get("variants")),
        "subject": subject,
        "cadence_min": _num(options.get("cadence_min"), 5.0, 1.0, 30.0),
        "readings_window_min": int(_num(options.get("readings_window_min"), 180, 30, 720)),
        "seed_history_hours": _num(options.get("seed_history_hours"), 8.0, 0.0, 24.0),
    }


def _round(value: object, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _cycle_frame(index: int, label: str, cycle) -> dict:
    return {
        "type": "cycle",
        "variant": index,
        "label": label,
        "ts": cycle.ts,
        "glucose": _round(cycle.glucose, 2),
        "rate": _round(cycle.rate),
        "smb": _round(cycle.smb),
        "insulin_req": _round(cycle.insulin_req),
        "final_isf": _round(cycle.final_isf, 2),
        "factors": dict(cycle.factors),
        "iob": _round(cycle.iob),
        "eventual_bg": _round(cycle.eventual_bg, 1),
        "sensitivity_ratio": _round(cycle.sensitivity_ratio),
        "reason": str(cycle.reason or "")[-220:],
    }


def _real_basal_units(trace: DayTrace) -> float:
    """Integrate the recorded temp basals and the profile basal over the import window."""
    changes: dict[int, float] = {
        int(trace.window_start): float(
            trace.profile_at(trace.window_start).basal_at(trace.window_start)
        )
    }
    temps = sorted(
        (
            treatment
            for treatment in trace.treatments
            if treatment.kind == "temp_basal"
            and trace.window_start <= treatment.ts <= trace.window_end
        ),
        key=lambda item: item.ts,
    )
    for treatment in temps:
        changes[int(treatment.ts)] = float(treatment.amount)
        end = int(treatment.ts + treatment.duration_min * 60_000)
        if end <= trace.window_end:
            changes[end] = float(trace.profile_at(end).basal_at(end))
    times = sorted(changes)
    total = 0.0
    for index, ts in enumerate(times):
        end = times[index + 1] if index + 1 < len(times) else int(trace.window_end)
        if end > ts:
            total += changes[ts] * (end - ts) / 3_600_000
    return total


def _real_insulin(trace: DayTrace) -> dict:
    """Recorded manual boluses, loop SMBs and basal inside the import window."""
    manual_units = 0.0
    manual_count = 0
    smb_units = 0.0
    smb_count = 0
    events: list[list] = []
    for treatment in trace.treatments:
        if treatment.kind != "bolus" or not (
            trace.window_start <= treatment.ts <= trace.window_end
        ):
            continue
        if treatment.origin == "loop":
            smb_units += treatment.amount
            smb_count += 1
            events.append([int(treatment.ts), float(treatment.amount), "SMB"])
        else:
            manual_units += treatment.amount
            manual_count += 1
            events.append([int(treatment.ts), float(treatment.amount), "NORMAL"])
    events.sort()
    return {
        "manual_units": round(manual_units, 3),
        "manual_count": manual_count,
        "smb_units": round(smb_units, 3),
        "smb_count": smb_count,
        "basal_units": round(_real_basal_units(trace), 3),
        "events": events,
    }


def _integrate_basal(cycles: list, window_end: int) -> float:
    """Basal units from each cycle rate over the delta to the next cycle and the window tail."""
    units = 0.0
    for index, cycle in enumerate(cycles):
        rate = cycle.rate
        if rate is None:
            continue
        start = int(cycle.ts)
        following = int(cycles[index + 1].ts) if index + 1 < len(cycles) else int(window_end)
        end = max(start, min(int(window_end), following))
        units += float(rate) * (end - start) / 3_600_000
    return units


def _variant_metrics(spec: dict, result) -> dict:
    cycles = list(result.cycles)
    smb_units = sum(cycle.smb for cycle in cycles)
    basal_units = _integrate_basal(cycles, int(spec["trace"].window_end))
    real = _real_insulin(spec["trace"])
    manual_units = float(real["manual_units"])
    iob_values = [cycle.iob for cycle in cycles if cycle.iob is not None]
    metrics = {
        "cycles": len(cycles),
        "smb_count": sum(1 for cycle in cycles if cycle.smb > 0),
        "smb_units": round(smb_units, 3),
        "manual_units": round(manual_units, 3),
        "basal_units": round(basal_units, 3),
        "total_units": round(smb_units + basal_units + manual_units, 3),
        "iob_mean": round(sum(iob_values) / len(iob_values), 3) if iob_values else None,
    }
    if spec["mode"] == "replay":
        metrics["skipped_cycles"] = len(result.skipped)
        return metrics
    glucose = [cycle.glucose for cycle in cycles]
    if glucose:
        summary = compute_metrics(np.array(glucose, dtype=float), []).as_dict()
        metrics.update({
            "mean_glucose": round(summary["mean_glucose"], 1),
            "tir_70_180": round(summary["tir_70_180"], 4),
            "tbr_70": round(summary["tbr_70"], 4),
            "tbr_54": round(summary["tbr_54"], 4),
            "lbgi": round(summary["lbgi"], 3),
        })
    metrics["glucose_offset"] = round(float(result.glucose_offset), 2)
    metrics["match"] = dataclasses.asdict(result.match)
    return metrics


def whatif_run(spec: dict, channel: "queue.Queue[dict]") -> None:
    trace = spec["trace"]
    variants = spec["variants"]
    try:
        channel.put({
            "type": "start",
            "mode": spec["mode"],
            "trace_id": spec["trace_id"],
            "window_start": trace.window_start,
            "window_end": trace.window_end,
            "variants": [variant.label for variant in variants],
            "cadence_min": spec["cadence_min"],
            "prefs_loaded": spec["prefs_loaded"],
            "real": [
                [int(point.ts), float(point.mgdl)] for point in trace.entries
                if trace.window_start <= point.ts <= trace.window_end
            ],
            "real_insulin": _real_insulin(trace)["events"],
        })
        results = []
        for index, variant in enumerate(variants):
            channel.put({
                "type": "variant",
                "index": index,
                "label": variant.label,
                "algorithm": variant.algorithm,
                "smb": variant.smb,
            })
            with Bridge() as bridge:
                if spec["mode"] == "replay":
                    result = run_replay(
                        trace, spec["prefs"], spec["insulin"], variant, bridge,
                        cadence_min=spec["cadence_min"],
                        readings_window_min=spec["readings_window_min"],
                        seed_history_hours=spec["seed_history_hours"],
                    )
                else:
                    result = run_model(
                        trace, spec["prefs"], spec["insulin"], variant, bridge,
                        subject=spec["subject"],
                    )
            for cycle in result.cycles:
                channel.put(_cycle_frame(index, variant.label, cycle))
            results.append({
                "index": index,
                "label": variant.label,
                "algorithm": variant.algorithm,
                "smb": variant.smb,
                "metrics": _variant_metrics(spec, result),
                "warnings": list(result.warnings),
            })
        channel.put({
            "type": "done",
            "results": results,
            "real": _real_insulin(trace),
        })
    except Exception as exc:
        channel.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"[-500:]})


def worker(run_id: str, spec: dict) -> None:
    channel = STREAMS[run_id]
    try:
        subject = spec["subject"]
        auto_isf = spec["auto_isf"]
        scenario, pump_config = build_scenario(spec["scenario"])
        seed = zlib.crc32(subject.name.encode()) & 0xFFFF
        if spec["variability"]:
            scenario = dataclasses.replace(
                scenario, si_multipliers=si_day(seed).multipliers, dawn_egp=1.5,
            )
        profile = profile_for(
            subject, auto_isf, spec["isf_source"], spec["smb_ratio_3min"], spec["algorithm"],
        )
        delay = 0.2 / max(spec["speed"], 0.1)

        channel.put({
            "type": "start", "subject": subject.name, "scenario": spec["scenario"],
            "arm": spec["arm"], "duration_min": scenario.duration_min,
            "cadence_min": 5.0, "meals": [[float(t), float(g)] for t, g in scenario.meals],
            "params": spec["echo"],
        })

        def on_cycle(result) -> None:
            channel.put({
                "type": "cycle",
                "time": result.time[-1],
                "cgm": result.cgm[-1],
                "plasma": result.glucose[-1],
                "iob": result.iob[-1],
                "cob": result.cob[-1],
                "rate": result.rate[-1],
                "sens_ratio": result.variable_sens[-1] / profile.sens,
                "smb": result.dose_events[-1][1] if result.dose_events and result.dose_events[-1][2] == "bolus" else 0.0,
                "reason": result.reason[-1][-220:],
            })
            if delay > 0:
                time.sleep(delay)

        with Bridge() as bridge:
            init_bridge(bridge, subject, spec["prefs"], INSULIN, profile, scenario)
            loop = run_closed_loop(
                subject, bridge, profile, scenario, seed=seed, residuals=RESIDUALS,
                pump_config=pump_config, on_cycle=on_cycle,
                temp_mode=spec["temp_mode"], noise_kind=spec["noise_kind"],
                noise_scale=spec["noise_scale"], noise_sigma=spec["noise_sigma"],
                noise_rho=spec["noise_ar1"],
            )
        metrics = compute_metrics(np.array(loop.cgm), loop.dose_events).as_dict()
        channel.put({"type": "done", "metrics": {k: round(v, 4) for k, v in metrics.items()}})
    except Exception as exc:  # noqa: BLE001 - surface any failure to the browser
        channel.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def log_message(self, fmt, *args):
        if "/api/" in self.path:
            super().log_message(fmt, *args)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _payload(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
            return None
        if not isinstance(payload, dict):
            self._json({"error": "payload must be an object"}, 400)
            return None
        return payload

    def _serve_events(self, channel: "queue.Queue[dict]", run_id: str, store: dict) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    frame = channel.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode("utf-8"))
                self.wfile.flush()
                if frame.get("type") in ("done", "error"):
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.close_connection = True
            with WHATIF_LOCK:
                store.pop(run_id, None)
                WHATIF_RUN_TRACE.pop(run_id, None)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/whatif/options":
            self._json({
                "modes": list(WHATIF_MODES),
                "algorithms": list(WHATIF_ALGORITHMS),
                "smb": [True, False],
                "defaults": {
                    "days": WHATIF_DEFAULT_DAYS,
                    "max_days": WHATIF_MAX_DAYS,
                    "max_variants": WHATIF_MAX_VARIANTS,
                    "cadence_min": 5.0,
                },
                "prefs": {
                    key: DEFAULT_PREFS.get(key, BASE_PREFS.get(key))
                    for key in UI_PREF_KEYS
                },
            })
            return
        if path.startswith("/api/whatif/trace/"):
            _prune_whatif(time.time())
            trace_id = path.removeprefix("/api/whatif/trace/").strip("/")
            with WHATIF_LOCK:
                trace = TRACES.get(trace_id)
                settings = dict(WHATIF_SETTINGS.get(trace_id) or {})
            if trace is None:
                self._json({"error": "unknown trace"}, 404)
                return
            self._json(_whatif_summary(trace, bool(settings.get("prefs_loaded"))))
            return
        if path == "/api/whatif/stream":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            run_id = (query.get("id") or [""])[0]
            with WHATIF_LOCK:
                channel = WHATIF_STREAMS.get(run_id)
            if channel is None:
                self._json({"error": "unknown run"}, 404)
                return
            self._serve_events(channel, run_id, WHATIF_STREAMS)
            return
        if self.path.startswith("/api/options"):
            self._json({
                "base_subjects": [name for name, _ in sorted(load_subjects().items())],
                "scenarios": [{"id": key, "label": label} for key, label in SCENARIO_LABELS.items()],
                "arms": ["off", "on", "dynisf"],
                "defaults": DEFAULTS,
                "prefs": {
                    key: DEFAULT_PREFS.get(key, BASE_PREFS.get(key))
                    for key in UI_PREF_KEYS
                },
            })
            return
        if self.path.startswith("/api/stream"):
            run_id = self.path.split("id=", 1)[1] if "id=" in self.path else ""
            channel = STREAMS.get(run_id)
            if channel is None:
                self._json({"error": "unknown run"}, 404)
                return
            self._serve_events(channel, run_id, STREAMS)
            return
        super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/whatif/import":
            payload = self._payload()
            if payload is None:
                return
            try:
                summary, trace_id = whatif_fetch(payload)
            except WhatifError as exc:
                self._json({"error": str(exc)}, exc.status)
                return
            except Exception as exc:
                self._json({"error": _scrub(
                    f"{type(exc).__name__}: {exc}",
                    payload.get("url"), payload.get("token"),
                    payload.get("api_secret"), payload.get("password"),
                )}, 500)
                return
            self._json({"trace_id": trace_id, "summary": summary})
            return
        if path == "/api/whatif/run":
            payload = self._payload()
            if payload is None:
                return
            try:
                spec = _whatif_run_spec(payload)
            except WhatifError as exc:
                self._json({"error": str(exc)}, exc.status)
                return
            run_id = uuid.uuid4().hex[:12]
            channel: "queue.Queue[dict]" = queue.Queue()
            with WHATIF_LOCK:
                WHATIF_STREAMS[run_id] = channel
                WHATIF_RUN_TRACE[run_id] = spec["trace_id"]
            threading.Thread(target=whatif_run, args=(spec, channel), daemon=True).start()
            self._json({"run_id": run_id})
            return
        if path != "/api/start":
            self._json({"error": "not found"}, 404)
            return
        payload = self._payload()
        if payload is None:
            return
        subject_name = payload.get("subject")
        scenario = payload.get("scenario")
        arm = payload.get("arm", "off")
        speed = float(payload.get("speed", 4))
        if scenario not in SCENARIO_LABELS or arm not in ("off", "on", "dynisf"):
            self._json({"error": "bad parameters"}, 400)
            return
        try:
            params, prefs = resolve_params(payload)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        cohort = load_cohort(params["cohort_copies"], params["cohort_seed"])
        if subject_name not in cohort:
            self._json({"error": f"unknown subject {subject_name!r} for this cohort"}, 400)
            return
        spec = {
            "subject": cohort[subject_name],
            "scenario": scenario,
            "auto_isf": arm == "on",
            "arm": arm,
            "algorithm": "dynisf" if arm == "dynisf" else "autoisf",
            "speed": speed,
            "prefs": prefs,
            "isf_source": params["isf_source"],
            "temp_mode": params["temp_mode"],
            "variability": params["variability"],
            "smb_ratio_3min": params["smb_ratio_3min"],
            "noise_kind": params["noise"],
            "noise_scale": params["noise_scale"],
            "noise_sigma": params["noise_sigma"],
            "noise_ar1": params["noise_ar1"],
            "echo": {key: value for key, value in params.items()},
        }
        run_id = uuid.uuid4().hex[:12]
        STREAMS[run_id] = queue.Queue()
        threading.Thread(target=worker, args=(run_id, spec), daemon=True).start()
        self._json({"id": run_id, "params": params})

    def do_DELETE(self):
        path = urllib.parse.urlsplit(self.path).path
        prefix = "/api/whatif/trace/"
        if not path.startswith(prefix):
            self._json({"error": "not found"}, 404)
            return
        trace_id = path.removeprefix(prefix).strip("/")
        with WHATIF_LOCK:
            exists = trace_id in TRACES
            if exists:
                _drop_whatif(trace_id)
        if not exists:
            self._json({"error": "unknown trace"}, 404)
            return
        self._json({"deleted": True})


def main() -> int:
    global DEFAULTS, DEFAULT_PREFS
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("SIM_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("SIM_PORT", 8766)))
    ap.add_argument("--isf-source", default="rule", choices=("rule", "model"))
    ap.add_argument("--temp-mode", default="planned", choices=("planned", "cycle"))
    ap.add_argument("--variability", action="store_true")
    add_options(ap)
    args = ap.parse_args()
    DEFAULT_PREFS = apply_prefs({}, args.pref)
    noise = noise_kwargs(args)
    DEFAULTS = {
        "cohort_copies": args.cohort_copies,
        "cohort_seed": args.cohort_seed,
        "noise": noise["noise_kind"],
        "noise_scale": noise["noise_scale"],
        "noise_sigma": noise["noise_sigma"],
        "noise_ar1": noise["noise_rho"],
        "isf_source": args.isf_source,
        "temp_mode": args.temp_mode,
        "variability": args.variability,
        "smb_ratio_3min": 0.3,
    }
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"live demo: http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
