#!/usr/bin/env python3
"""Live demo server: runs the closed loop and streams cycles over SSE (stdlib only)."""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import queue
import sys
import threading
import time
import uuid
import zlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from bridge.client import Bridge, insulin_from_settings, load_prefs
from runner.closed_loop import init_bridge, run_closed_loop
from runner.export_demo import build_scenario, profile_for
from runner.metrics import compute_metrics
from runner.options import add_options, apply_prefs, load_cohort, noise_kwargs
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

STREAMS: dict[str, "queue.Queue[dict]"] = {}
DEFAULTS: dict = {}
DEFAULT_PREFS: dict = {}


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

    def do_GET(self):
        if self.path.startswith("/api/options"):
            self._json({
                "base_subjects": [name for name, _ in sorted(load_subjects().items())],
                "scenarios": [{"id": key, "label": label} for key, label in SCENARIO_LABELS.items()],
                "arms": ["off", "on"],
                "defaults": DEFAULTS,
            })
            return
        if self.path.startswith("/api/stream"):
            run_id = self.path.split("id=", 1)[1] if "id=" in self.path else ""
            channel = STREAMS.get(run_id)
            if channel is None:
                self._json({"error": "unknown run"}, 404)
                return
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
                STREAMS.pop(run_id, None)
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/start":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
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
