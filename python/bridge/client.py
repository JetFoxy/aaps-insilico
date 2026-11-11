#!/usr/bin/env python3
"""JSON-lines client for the JVM bridge (sim-bridge in the pinned AAPS worktree)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_CLASSPATH = Path(
    os.environ.get("SIM_CLASSPATH", Path.home() / "Projects/Dia/AndroidAPS-w1/sim-bridge/build/sim-classpath.txt")
)
DEFAULT_JAVA = os.environ.get(
    "SIM_JAVA", "/Library/Java/JavaVirtualMachines/sdkman-21.jdk/Contents/Home/bin/java"
)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PREFS = ROOT / "data" / "fixtures" / "prefs.example.json"

_fallback_warned = False


def resolve_prefs_path(path: Path | str) -> Path:
    """Return `path` if it exists, otherwise the shipped example preferences."""
    global _fallback_warned
    resolved = Path(path)
    if resolved.exists():
        return resolved
    if not EXAMPLE_PREFS.exists():
        raise FileNotFoundError(f"settings not found: {resolved} (and no {EXAMPLE_PREFS})")
    if not _fallback_warned:
        print(f"warning: {resolved} not found, using {EXAMPLE_PREFS.name}", file=sys.stderr)
        _fallback_warned = True
    return EXAMPLE_PREFS


class BridgeError(RuntimeError):
    pass


class Bridge:

    def __init__(self, classpath: Path | str | None = None):
        path = Path(classpath or os.environ.get("SIM_CLASSPATH", DEFAULT_CLASSPATH))
        if not path.exists():
            raise BridgeError(f"classpath file not found: {path}; run :sim-bridge:simClasspath")
        self.process = subprocess.Popen(
            [os.environ.get("SIM_JAVA", DEFAULT_JAVA), "-cp", path.read_text().strip(), "app.aaps.sim.SimBridgeKt"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def request(self, payload: dict) -> dict:
        if self.process.stdin is None or self.process.stdout is None:
            raise BridgeError("bridge process is not running")
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr else ""
            raise BridgeError(f"bridge died: {stderr[-4000:]}")
        response = json.loads(line)
        if "error" in response:
            raise BridgeError(response["error"])
        return response

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def __enter__(self) -> "Bridge":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def load_prefs(path: Path) -> dict:
    data = json.loads(resolve_prefs_path(path).read_text())
    prefs = {}
    for key, value in data.items():
        if key == "insulin_configuration":
            continue
        prefs[key] = value
    return prefs


def load_profiles(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_treatments(path: Path) -> tuple[list[dict], list[dict]]:
    boluses, temps = [], []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["type"] in ("Correction Bolus", "Meal Bolus"):
            if not row.get("insulin"):
                continue
            boluses.append({
                "kind": "bolus",
                "ts": row["ts"],
                "amount": row["insulin"],
                "type": row.get("subtype") or "NORMAL",
                "isValid": row.get("isValid", True),
                "icfg": row.get("icfg"),
            })
        elif row["type"] == "Temp Basal":
            absolute = row.get("absolute")
            temps.append({
                "kind": "temp",
                "ts": row["ts"],
                "duration": int(row.get("durationInMilliseconds") or (int(row.get("duration") or 0) * 60_000)),
                "rate": row.get("rate") or 0.0,
                "isAbsolute": absolute is not None,
                "isValid": row.get("isValid", True),
            })
    boluses.sort(key=lambda r: r["ts"])
    temps.sort(key=lambda r: r["ts"])
    return boluses, temps


def load_carbs(path: Path) -> list[dict]:
    carbs = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row["type"] in ("Meal Bolus", "Carb Correction") and row.get("carbs"):
            carbs.append({"kind": "carbs", "ts": row["ts"], "amount": row["carbs"], "duration": 0})
    carbs.sort(key=lambda r: r["ts"])
    return carbs


def load_golden(root: Path) -> tuple[list[dict], list[tuple[int, float]], list[dict], list[dict], list[dict]]:
    cycles = [json.loads(line) for line in (root / "cycles.jsonl").read_text().splitlines() if line.strip()]
    cgm = [tuple(json.loads(line).values()) for line in (root / "cgm.jsonl").read_text().splitlines() if line.strip()]
    profiles = load_profiles(root / "profiles.jsonl")
    boluses, temps = load_treatments(root / "treatments.jsonl")
    return cycles, cgm, profiles, boluses, temps


def insulin_from_settings(settings_path: Path, label: str) -> dict:
    data = json.loads(resolve_prefs_path(settings_path).read_text())
    raw = json.loads(data["insulin_configuration"])
    for insulin in raw["insulin"]:
        if insulin["insulinLabel"] == label:
            return {
                "label": insulin["insulinLabel"],
                "endTime": insulin["insulinEndTime"],
                "peakTime": insulin["insulinPeakTime"],
                "concentration": insulin["concentration"],
            }
    raise KeyError(f"insulin not found: {label}")
