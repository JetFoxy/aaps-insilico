#!/usr/bin/env python3
"""M2: summarize the derived cohort against the published S2017 Table 1."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simcore.population import cohort_rows, summary

PUBLISHED = {
    "adult": {"weight_kg": (75.2, 12.1), "tdi_per_kg": (0.56, 0.16), "cr": (16.9, 7.9), "fasting_bg": (119.1, 7.1)},
    "adolescent": {"weight_kg": (50.8, 8.8), "tdi_per_kg": (0.68, 0.22), "cr": (14.5, 7.1), "fasting_bg": (120.8, 6.6)},
    "child": {"weight_kg": (30.7, 5.3), "tdi_per_kg": (0.56, 0.16), "cr": (21.8, 8.7), "fasting_bg": (119.6, 6.0)},
}


def main() -> int:
    out = Path(__file__).resolve().parents[2] / "out"
    out.mkdir(exist_ok=True)
    rows = cohort_rows()
    report = {}
    for group in ("adult", "adolescent", "child"):
        report[group] = {}
        for field in ("weight_kg", "tdi_per_kg", "cr", "fasting_bg"):
            stats = summary(rows, group, field)
            report[group][field] = stats
            if not stats:
                print(f"{group:11s} {field:12s} n=0")
                continue
            pub_mean, pub_sd = PUBLISHED[group][field]
            z = (stats["mean"] - pub_mean) / pub_sd
            print(
                f"{group:11s} {field:12s} n={stats['n']:.0f} "
                f"mean={stats['mean']:.1f} sd={stats['sd']:.1f} "
                f"range={stats['min']:.1f}-{stats['max']:.1f}  "
                f"published {pub_mean:.1f} (SD {pub_sd:.1f})  z={z:+.2f}"
            )
    with (out / "m2_population.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(f"written {out / 'm2_population.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
