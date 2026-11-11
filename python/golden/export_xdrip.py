#!/usr/bin/env python3
"""Export xDrip BgReadings into the redacted golden CGM series.

AAPS receives EXTRA_BG_ESTIMATE from xDrip, which is the value xDrip stores as
`calculated_value`; AAPS rounds it to an integer before storing it as a GV.
Only timestamp and value leave data/raw.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("data/golden/cgm_db.jsonl"))
    ap.add_argument("--column", default="calculated_value")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    column = args.column
    if column not in ("calculated_value", "raw_data", "age_adjusted_raw_value", "filtered_calculated_value"):
        raise SystemExit(f"unsupported column: {column}")
    rows = con.execute(
        f"select timestamp, {column} from BgReadings "
        f"where {column} is not null and {column} > 0 order by timestamp"
    ).fetchall()
    digest = hashlib.sha256()
    seen = set()
    kept = 0
    with args.out.open("w", encoding="utf-8") as handle:
        for timestamp, value in rows:
            if timestamp in seen:
                continue
            seen.add(timestamp)
            line = json.dumps({"ts": int(timestamp), "sgv": round(float(value))}, sort_keys=True) + "\n"
            digest.update(line.encode())
            handle.write(line)
            kept += 1
    print(f"wrote {kept} readings to {args.out} (sha256 {digest.hexdigest()[:12]})")

    sensors_out = args.out.parent / "sensors_db.jsonl"
    sensors = con.execute(
        "select started_at, stopped_at from Sensors where started_at is not null order by started_at"
    ).fetchall()
    with sensors_out.open("w", encoding="utf-8") as handle:
        for started, stopped in sensors:
            handle.write(json.dumps({"start": int(started), "stop": int(stopped) if stopped else None}, sort_keys=True) + "\n")
    print(f"wrote {len(sensors)} sensor sessions to {sensors_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
