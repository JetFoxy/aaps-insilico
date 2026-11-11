#!/usr/bin/env python3
"""Normalise raw Nightscout exports into the redacted W1 golden set.

Reads data/raw (devicestatus, treatments, entries, profile) and writes
data/golden/{cycles,cgm,treatments,profiles}.jsonl plus manifest.json.

Personal data never leaves data/raw: device names, uploader ids, Nightscout
_ids, notes, locations and profile names are dropped, and profile names are
replaced by P1..Pn with the mapping kept in data/raw/redaction.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "golden/v1"
PROFILE_FIELDS = ("basal", "carbratio", "carbs_hr", "delay", "dia", "sens", "target_low", "target_high", "units", "timezone")
TREATMENT_KEEP = {
    "Temp Basal": ("rate", "duration", "durationInMilliseconds", "isValid", "absolute"),
    "Correction Bolus": ("insulin", "type", "icfg"),
    "Meal Bolus": ("insulin", "carbs", "type", "icfg"),
    "Carb Correction": ("carbs",),
    "Temporary Target": ("targetTop", "targetBottom", "duration", "units"),
    "Profile Switch": ("duration", "profile"),
    "Exercise": ("duration", "notes"),
    "Announcement": ("duration", "notes"),
    "Insulin Change": (),
    "Site Change": (),
    "Sensor Change": (),
    "Pump Battery Change": (),
    "OpenAPS Offline": ("duration",),
    "Bolus Wizard": ("glucose", "units"),
    "Note": (),
}


def load(pattern: str, raw: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(raw.glob(pattern)):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            out.extend(data)
    return out


def epoch_ms(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    if text.isdigit():
        return int(text)
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def uploader_name(device: str | None) -> str:
    name = (device or "").strip()
    if name.startswith("Libre2"):
        return "libre2"
    if name.startswith("xDrip-LibreReceiver Libre2 Native"):
        return "native"
    if name.startswith("xDrip-LibreReceiver"):
        return "receiver"
    if name.startswith("GS1Sb"):
        return "gs1sb"
    if name.startswith("iGlucco"):
        return "iglucco"
    if name.startswith("Bluetooth Glucose Meter"):
        return "meter"
    return "unknown"


def classify_cgm(device: str | None) -> str:
    name = (device or "").strip()
    if name.startswith("iGlucco"):
        return "other"
    if name.startswith("Bluetooth Glucose Meter"):
        return "meter"
    if name.startswith("Libre2") or name.startswith("xDrip-LibreReceiver") or name.startswith("GS1Sb"):
        return "xdrip"
    return "unknown"


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_profile_map(profiles: list[dict], raw: Path) -> dict[str, str]:
    names = sorted({p.get("defaultProfile") for p in profiles if p.get("defaultProfile")})
    mapping = {name: f"P{i + 1}" for i, name in enumerate(names)}
    with (raw / "redaction.json").open("w", encoding="utf-8") as f:
        json.dump({"profiles": mapping}, f, ensure_ascii=False, indent=2)
    return mapping


CONSOLE_PATTERNS = {
    "iobTH": re.compile(r"User setting iobTH=(\d+)%"),
    "maxSMBBasalMinutes": re.compile(r"profile\.maxSMBBasalMinutes:\s*([\d.]+)"),
    "maxUAMSMBBasalMinutes": re.compile(r"profile\.maxUAMSMBBasalMinutes:\s*([\d.]+)"),
    "version": re.compile(r"start AutoISF ([\d.]+)"),
}


def parse_console(lines: list[str]) -> dict:
    text = "\n".join(lines)
    seen: dict = {}
    for name, pattern in CONSOLE_PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = match.group(1)
            seen[name] = value if name == "version" else float(value) if "." in value else int(value)
    activity = re.search(r"Activity monitor disabled: ([^;\n]+)", text)
    return {"settings_seen": seen, "activityMonitor": activity.group(1).strip() if activity else None}


def scrub(text: str, mapping: dict[str, str]) -> str:
    for name, alias in mapping.items():
        text = text.replace(name, alias)
        text = text.replace(name.strip(), alias)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="data/golden")
    ap.add_argument("--source", default="site1")
    args = ap.parse_args()

    raw, out = Path(args.raw), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    profiles = load("profile*.json", raw)
    mapping = build_profile_map(profiles, raw)

    cycles: dict[int, dict] = {}
    seen_ds = 0
    for rec in load("devicestatus_*.json", raw):
        seen_ds += 1
        openaps = rec.get("openaps") or {}
        suggested = openaps.get("suggested") or {}
        if not suggested.get("timestamp"):
            continue
        ts = epoch_ms(suggested["timestamp"])
        enacted = openaps.get("enacted") or {}
        active_profile = ((rec.get("pump") or {}).get("extended") or {}).get("ActiveProfile")
        entry = {
            "id": iso(ts),
            "ts": ts,
            "algo": suggested.get("algorithm"),
            "runningAutoIsf": suggested.get("runningAutoIsf"),
            "runningDynamicIsf": suggested.get("runningDynamicIsf"),
            "bg": suggested.get("bg"),
            "eventualBG": suggested.get("eventualBG"),
            "targetBG": suggested.get("targetBG"),
            "insulinReq": suggested.get("insulinReq"),
            "cob": suggested.get("COB"),
            "iob": suggested.get("IOB"),
            "sensRatio": suggested.get("sensitivityRatio"),
            "rate": enacted.get("rate"),
            "duration": enacted.get("duration"),
            "smb": enacted.get("smb"),
            "rateSuggested": suggested.get("rate"),
            "durationSuggested": suggested.get("duration"),
            "unitsSuggested": suggested.get("units"),
            "predBGs": suggested.get("predBGs") or {},
            "reason": scrub(suggested.get("reason") or "", mapping),
            "consoleLog": [scrub(str(x), mapping) for x in (suggested.get("consoleLog") or [])],
            "consoleError": [scrub(str(x), mapping) for x in (suggested.get("consoleError") or [])],
            "profile": mapping.get(active_profile),
            "appVersion": ((rec.get("pump") or {}).get("extended") or {}).get("Version"),
            "baseBasal": ((rec.get("pump") or {}).get("extended") or {}).get("BaseBasalRate"),
            "tempRate": ((rec.get("pump") or {}).get("extended") or {}).get("TempBasalAbsoluteRate"),
            "tempStart": ((rec.get("pump") or {}).get("extended") or {}).get("TempBasalStart"),
        }
        entry.update(parse_console(entry["consoleLog"] + entry["consoleError"]))
        previous = cycles.get(ts)
        if previous is None or (previous["rate"] is None and entry["rate"] is not None):
            cycles[ts] = entry

    treatments = []
    seen_treatments: set[tuple] = set()
    for rec in load("treatments_*.json", raw):
        kind = rec.get("eventType")
        if kind not in TREATMENT_KEEP or rec.get("isValid") is False:
            continue
        ts = epoch_ms(rec.get("date") or rec.get("created_at"))
        if ts is None:
            continue
        item = {"ts": ts, "type": kind, "createdAt": epoch_ms(rec.get("created_at")) or ts}
        for field in TREATMENT_KEEP[kind]:
            if field == "profile":
                item["profile"] = mapping.get(rec.get("profileName"))
            elif field == "notes":
                item["notes"] = scrub(str(rec.get("notes") or ""), mapping)
            elif field == "type":
                item["subtype"] = rec.get("type")
            else:
                item[field] = rec.get(field)
        if kind in ("Meal Bolus", "Carb Correction") and not item.get("carbs") and not item.get("insulin"):
            continue
        key = (ts, kind, item.get("insulin"), item.get("carbs"), item.get("rate"), item.get("duration"))
        if key in seen_treatments:
            continue
        seen_treatments.add(key)
        treatments.append(item)
    treatments.sort(key=lambda t: t["ts"])

    cgm: dict[int, dict] = {}
    for rec in load("entries_*.json", raw):
        if rec.get("type") != "sgv":
            continue
        ts = epoch_ms(rec.get("date"))
        if ts is None or rec.get("sgv") is None:
            continue
        cgm.setdefault(ts, {"ts": ts, "sgv": rec["sgv"], "source": classify_cgm(rec.get("device")), "uploader": uploader_name(rec.get("device"))})
    cgm_rows = [cgm[k] for k in sorted(cgm)]

    profile_rows = []
    for rec in profiles:
        name = rec.get("defaultProfile")
        store = (rec.get("store") or {}).get(name) or {}
        if not store:
            continue
        start = epoch_ms(rec.get("startDate") or rec.get("created_at"))
        profile_rows.append({"ts": start, "profile": mapping.get(name),
                             **{f: store.get(f) for f in PROFILE_FIELDS}})
    profile_rows.sort(key=lambda r: r["ts"] or 0)

    def write_jsonl(name: str, rows: list[dict]) -> str:
        path = out / name
        digest = hashlib.sha256()
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                line = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                digest.update(line.encode())
                f.write(line)
        return digest.hexdigest()[12:]

    cycle_rows = [cycles[k] for k in sorted(cycles)]
    files = {
        "cycles.jsonl": write_jsonl("cycles.jsonl", cycle_rows),
        "cgm.jsonl": write_jsonl("cgm.jsonl", cgm_rows),
        "treatments.jsonl": write_jsonl("treatments.jsonl", treatments),
        "profiles.jsonl": write_jsonl("profiles.jsonl", profile_rows),
    }

    algorithms = Counter((c["algo"], bool(c["runningAutoIsf"]), bool(c["runningDynamicIsf"])) for c in cycle_rows)
    manifest = {
        "schema": SCHEMA,
        "source": args.source,
        "counts": {
            "devicestatus_records": seen_ds,
            "cycles": len(cycle_rows),
            "cycles_enacted": sum(1 for c in cycle_rows if c["rate"] is not None),
            "cycles_cob_zero": sum(1 for c in cycle_rows if not c["cob"]),
            "cgm": len(cgm_rows),
            "treatments": len(treatments),
            "profiles": len(profile_rows),
        },
        "algorithms": {f"{a}|autoisf={ai}|dynisf={d}": n for (a, ai, d), n in algorithms.most_common()},
        "span": [cycle_rows[0]["id"], cycle_rows[-1]["id"]] if cycle_rows else [],
        "files_sha256_12": files,
        "redacted": ["device", "uploader", "subject", "identifier", "_id", "notes", "location", "profile names"],
    }
    with (out / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps(manifest["counts"], indent=2))
    print("algorithms:", manifest["algorithms"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
