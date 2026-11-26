"""Pre-registered safety gate: criteria fixed before the run, with an exposure bound.

The criteria follow TimSim's gate (MIT, ``timsim/validation/gate.py``): each threshold is tied
to a hazard, the criteria and the design are hashed before the run, and a pass is reported
with the exposure it rests on. The rule-of-three upper bound is used when no severe event is
seen; it is the honest answer to "no events in this cohort".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))


@dataclass(frozen=True)
class Criterion:
    key: str
    summary: str
    limit: float
    direction: str
    hazard: str
    rationale: str

    def measure(self, records: list[dict]) -> float:
        values = np.array(
            [float(record[self.key]) for record in records
             if np.isfinite(float(record[self.key]))],
            dtype=float,
        )
        if values.size == 0:
            return float("nan")
        if self.summary == "median":
            return float(np.median(values))
        if self.summary == "p90":
            return float(np.percentile(values, 90))
        if self.summary == "worst":
            return float(values.max())
        if self.summary == "count_nonzero":
            return float(np.count_nonzero(values))
        raise ValueError(f"unknown summary {self.summary!r}")

    def holds(self, value: float) -> bool:
        if not np.isfinite(value):
            return False
        return value <= self.limit if self.direction == "at_most" else value >= self.limit


DEFAULT_CRITERIA = (
    Criterion(
        "minutes_below_54_per_day", "median", 14.4, "at_most",
        hazard="clinically significant hypoglycaemia across the cohort",
        rationale="1% of a day is the consensus target for time below 54 mg/dL",
    ),
    Criterion(
        "minutes_below_54_per_day", "worst", 60.0, "at_most",
        hazard="clinically significant hypoglycaemia in one subject",
        rationale="no subject may spend more than an hour a day below 54",
    ),
    Criterion(
        "longest_below_54_min", "worst", 120.0, "at_most",
        hazard="a prolonged severe low rather than a brief one",
        rationale="duration is the harmful part of a severe low",
    ),
    Criterion(
        "tir_70_180", "median", 0.70, "at_least",
        hazard="poor overall control",
        rationale="the consensus target for time in range",
    ),
    Criterion(
        "tbr_70", "worst", 0.10, "at_most",
        hazard="too much time below range in one subject",
        rationale="the consensus upper bound for time below 70 mg/dL",
    ),
)


def fingerprint(criteria=DEFAULT_CRITERIA, design: dict | None = None) -> str:
    payload = {
        "criteria": [asdict(criterion) for criterion in criteria],
        "design": design or {},
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def evaluate(
    records: list[dict],
    criteria=DEFAULT_CRITERIA,
    design: dict | None = None,
    days: float = 1.0,
) -> dict:
    subject_days = len(records) * float(days)
    severe_events = sum(
        1 for record in records
        if float(record.get("longest_below_54_min", 0.0)) >= 120.0
    )
    per_year = (3.0 if severe_events == 0 else float(severe_events)) / max(subject_days / 365.0, 1e-9)
    rows = []
    passed = True
    for criterion in criteria:
        value = criterion.measure(records)
        holds = criterion.holds(value)
        passed = passed and holds
        rows.append({
            "key": criterion.key,
            "summary": criterion.summary,
            "limit": criterion.limit,
            "direction": criterion.direction,
            "value": value,
            "holds": holds,
            "hazard": criterion.hazard,
        })
    return {
        "passed": passed,
        "fingerprint": fingerprint(criteria, design),
        "rows": rows,
        "exposure": {
            "subjects": len(records),
            "days": float(days),
            "subject_days": subject_days,
            "severe_events": severe_events,
            "upper_bound_per_subject_year": per_year,
        },
    }


def report(verdict: dict) -> str:
    lines = [
        f"# Safety gate — {'passed' if verdict['passed'] else 'failed'}",
        "",
        f"Fingerprint: `{verdict['fingerprint']}`",
        "",
        "| Criterion | Summary | Value | Limit | Holds | Hazard |",
        "|---|---|---|---|---|---|",
    ]
    for row in verdict["rows"]:
        lines.append(
            f"| `{row['key']}` | {row['summary']} | {row['value']:.3f} | "
            f"{row['limit']:.3f} | {'yes' if row['holds'] else 'no'} | {row['hazard']} |"
        )
    exposure = verdict["exposure"]
    lines += [
        "",
        f"Exposure: {exposure['subjects']} subjects × {exposure['days']:.1f} days = "
        f"{exposure['subject_days']:.0f} subject-days; severe events: {exposure['severe_events']}.",
        f"Upper bound with no severe events (rule of three): "
        f"{exposure['upper_bound_per_subject_year']:.1f} per subject-year.",
        "",
        "A pass is not evidence of safety: it is a pre-clinical gate against the listed hazards, "
        "with the exposure above.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="fault-matrix JSON with per-subject rows")
    parser.add_argument("--arm", default="dynisf", choices=("off", "autoisf", "dynisf"))
    parser.add_argument("--fault", default="baseline")
    parser.add_argument("--days", type=float, default=1.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    data = json.loads(Path(args.json).read_text())
    rows = data.get("rows", [])
    if not rows:
        raise SystemExit("the JSON has no per-subject rows; rerun fault_matrix with rows")
    if args.arm == "off":
        selected = [row for row in rows if row["fault"] == args.fault
                    and not row["auto_isf"] and row.get("algorithm", "autoisf") == "autoisf"]
    elif args.arm == "autoisf":
        selected = [row for row in rows if row["fault"] == args.fault
                    and row["auto_isf"] and row.get("algorithm", "autoisf") == "autoisf"]
    else:
        selected = [row for row in rows if row["fault"] == args.fault
                    and not row["auto_isf"] and row.get("algorithm") == "dynisf"]
    records = [row["metrics"] for row in selected]
    design = {"arm": args.arm, "fault": args.fault, "days": args.days, "n": len(records)}
    verdict = evaluate(records, design=design, days=args.days)
    text = report(verdict)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
