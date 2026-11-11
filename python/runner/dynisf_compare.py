#!/usr/bin/env python3
"""Compare autoISF off / on / DynISF on the same subjects and scenario."""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from bridge.client import Bridge, insulin_from_settings, load_prefs
from runner.a2_matrix import paired_stats
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.metrics import compute_metrics
from simcore.params import load_subject, load_subjects
from simcore.population import compute_cf, compute_cr, compute_tdi

PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
RESIDUALS = np.load(ROOT / "tests/fixtures/cgm_residuals.npy")
SCENARIO = Scenario(duration_min=24 * 60, meals=[(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)], meal_mode="fcl")
ARMS = ("off", "autoisf", "dynisf")
KEYS = ("tir_70_180", "tbr_70", "lbgi", "smb_count", "total_insulin", "insulin_while_falling_fraction")


def run_task(task: tuple[str, str]) -> dict:
    name, arm = task
    subject = load_subject(name)
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    profile = AlgorithmProfile(
        sens=compute_cf(tdi) or 50.0, carb_ratio=cr, target_bg=110.0,
        current_basal=subject.basal_u_per_min * 60,
        enable_auto_isf=(arm == "autoisf"),
        algorithm="dynisf" if arm == "dynisf" else "autoisf",
        tdd=tdi,
    )
    seed = zlib.crc32(name.encode()) & 0xFFFF
    with Bridge() as bridge:
        init_bridge(bridge, subject, PREFS, INSULIN, profile, SCENARIO)
        loop = run_closed_loop(subject, bridge, profile, SCENARIO, seed=seed, residuals=RESIDUALS)
    return {"subject": name, "arm": arm, "metrics": compute_metrics(np.array(loop.cgm), loop.dose_events).as_dict()}


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--subjects", type=int, default=6)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="out/dynisf_compare.json")
    args = ap.parse_args()

    names = [name for name, _ in sorted(load_subjects().items())][: args.subjects]
    tasks = [(name, arm) for arm in ARMS for name in names]
    started = time.time()
    with mp.Pool(processes=args.workers) as pool:
        results = pool.map(run_task, tasks, chunksize=1)
    table = {}
    for arm in ARMS:
        rows = [row["metrics"] for row in results if row["arm"] == arm]
        table[arm] = {key: float(np.mean([row[key] for row in rows])) for key in KEYS}
    by_key = {(row["subject"], row["arm"]): row["metrics"] for row in results}
    pairs = {}
    for left, right in (("off", "autoisf"), ("off", "dynisf"), ("autoisf", "dynisf")):
        rows = [{"off": by_key[(name, left)], "on": by_key[(name, right)]} for name in names]
        pairs[f"{left}_vs_{right}"] = {key: paired_stats(rows, key) for key in KEYS}
    payload = {
        "elapsed_s": round(time.time() - started, 1), "subjects": names,
        "table": table, "pairs": pairs, "rows": results,
    }
    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"{'arm':10s} {'TIR':>6s} {'TBR':>6s} {'LBGI':>6s} {'SMB':>5s} {'dose':>6s} {'fall':>6s}")
    for arm in ARMS:
        t = table[arm]
        print(f"{arm:10s} {t['tir_70_180']:6.3f} {t['tbr_70']:6.3f} {t['lbgi']:6.2f} "
              f"{t['smb_count']:5.0f} {t['total_insulin']:6.1f} {t['insulin_while_falling_fraction']:6.3f}")
    for label, block in pairs.items():
        print(f"== {label}")
        for key in ("tir_70_180", "tbr_70", "lbgi", "total_insulin", "insulin_while_falling_fraction"):
            st = block[key]
            print(f"   {key:30s} {st['off_mean']:.3f} -> {st['on_mean']:.3f} "
                  f"diff={st['diff_mean']:+.3f} p={st['p_wilcoxon']:.4f}")
    print(f"written {out} ({payload['elapsed_s']} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
