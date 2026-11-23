#!/usr/bin/env python3
"""Import a Nightscout window and print a summary that never contains secrets."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from nightscout.client import NightscoutClient, NightscoutError
from nightscout.prefs import PrefsDecryptError, decode_export
from nightscout.trace import DayTrace, build_trace

DAY_MS = 86_400_000
DEFAULT_DAYS = 3
MAX_DAYS = 14


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nightscout")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="read a Nightscout window and summarize it")
    importer.add_argument("--url", required=True)
    importer.add_argument("--token")
    importer.add_argument("--days", type=_positive_int, default=DEFAULT_DAYS)
    importer.add_argument("--prefs")
    importer.add_argument("--password")
    importer.add_argument("--out")
    return parser


def _summary(trace: DayTrace, prefs_loaded: bool) -> dict:
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


def _load_prefs(path: str, password: str | None) -> bool:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        raise PrefsDecryptError("cannot read settings export file") from None
    decode_export(text, password)
    return True


def _import_window(args: argparse.Namespace) -> int:
    days = min(args.days, MAX_DAYS)
    window_end = int(time.time() * 1000)
    window_start = window_end - days * DAY_MS
    prefs_loaded = _load_prefs(args.prefs, args.password) if args.prefs else False
    client = NightscoutClient(args.url, token=args.token)
    client.status()
    profiles = client.profile()
    entries = client.entries(window_start, window_end)
    treatments = client.treatments(window_start, window_end)
    devicestatus = client.devicestatus(window_start, window_end)
    trace = build_trace(
        entries=entries,
        treatments=treatments,
        profiles=profiles,
        devicestatus=devicestatus,
        window_start=window_start,
        window_end=window_end,
    )
    if days < args.days:
        trace.warnings.append(f"window capped at {MAX_DAYS} days")
    summary = _summary(trace, prefs_loaded)
    if args.out:
        payload = asdict(trace)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "import":
            return _import_window(args)
    except (NightscoutError, PrefsDecryptError) as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    return 2
