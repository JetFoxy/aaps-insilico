"""Fidelity scorecard: twelve signatures of the stand against TimSim's real ranges."""
from __future__ import annotations

import argparse
import dataclasses
import json
import multiprocessing as mp
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from bridge.client import Bridge, insulin_from_settings, load_prefs
from cgm.compression import CompressionConfig
from cgm.validate_sensors import load_trace
from runner.behaviour import BehaviourConfig
from runner.intake import INTAKE_SEED_SALT, intake_schedule
from runner.closed_loop import (
    AlgorithmProfile, Scenario, adaptive_profile_schedule, init_bridge, meals_for_days,
    run_closed_loop,
)
from runner.fidelity import SIGNATURES, evaluate, from_loop, in_real_range, report, trace
from runner.options import RESIDUALS
from simcore.variability import si_day
from simcore.params import load_subjects
from simcore.population import compute_cf, compute_cr, compute_tdi

MEALS = [(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)]
SETTINGS = ROOT / "data/raw/settings.json"
REAL_FILES = ("sibionics2.csv", "libre2.csv", "aidex.csv")


def meal_plan_for(mode: str, days: int, seed: int, body_weight: float):
    """One day's fixed three meals or the sourced seven-occasion template, repeated daily."""
    if mode == "template":
        return intake_schedule(days, seed ^ INTAKE_SEED_SALT, body_weight)
    return meals_for_days(MEALS, days), []


def compression_for(rate_per_30d: float):
    """Compression artefacts are opt-in; the rate is events per 30 days."""
    if rate_per_30d <= 0:
        return None
    return CompressionConfig(rate_per_30d=float(rate_per_30d))


def prefs() -> dict:
    return load_prefs(SETTINGS)


def insulin() -> dict:
    return insulin_from_settings(SETTINGS, "Lyumjev 45m 8h 100 ед")


def profile_for(subject) -> AlgorithmProfile:
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    return AlgorithmProfile(
        sens=compute_cf(tdi) or 50.0, carb_ratio=cr, target_bg=110.0,
        current_basal=subject.basal_u_per_min * 60, enable_auto_isf=False,
    )


def run_subject(task):
    name, subject, behaviour, days, isf_cv, cr_cv, readings_limit, compression_rate, meal_plan, variability = task
    profile = profile_for(subject)
    seed = zlib.crc32(name.encode()) & 0xFFFF
    meals, snacks = meal_plan_for(
        meal_plan, days, seed, float(getattr(subject, "BW", 70.0))
    )
    scenario = Scenario(
        duration_min=int(days) * 24 * 60,
        meals=meals, snacks=snacks,
        behaviour=BehaviourConfig() if behaviour else None,
        compression=compression_for(compression_rate),
    )
    if variability:
        scenario = dataclasses.replace(
            scenario, si_multipliers=si_day(seed).multipliers, dawn_egp=1.5,
        )
    schedule = adaptive_profile_schedule(profile, days, isf_cv, cr_cv, seed)
    with Bridge() as bridge:
        init_bridge(bridge, subject, prefs(), insulin(), profile, scenario)
        loop = run_closed_loop(
            subject, bridge, profile, scenario,
            seed=seed, residuals=RESIDUALS, noise_kind="residuals",
            profile_schedule=schedule or None, readings_limit=readings_limit,
        )
    item = from_loop(loop, plasma=True)
    item.pop("insulin", None)
    item.pop("sens", None)
    return from_loop(loop), item


def model_traces(
    subjects: dict,
    count: int,
    behaviour: bool = False,
    days: int = 1,
    isf_cv: float = 0.0,
    cr_cv: float = 0.0,
    readings_limit: int | None = None,
    workers: int = 1,
    compression_rate: float = 0.0,
    meal_plan: str = "fixed",
    variability: bool = False,
    runner=None,
) -> tuple[list[dict], list[dict]]:
    tasks = [
        (name, subjects[name], behaviour, days, isf_cv, cr_cv, readings_limit,
         compression_rate, meal_plan, variability)
        for name in sorted(subjects)[:count]
    ]
    run = runner or run_subject
    if workers > 1:
        with mp.Pool(processes=workers) as pool:
            pairs = pool.map(run, tasks, chunksize=1)
    else:
        pairs = [run(task) for task in tasks]
    return [pair[0] for pair in pairs], [pair[1] for pair in pairs]


def real_traces(directory: Path) -> dict[str, list[dict]]:
    result = {}
    for filename in REAL_FILES:
        path = directory / filename
        if not path.exists():
            continue
        stamps, values = load_trace(path)
        if stamps.size < 10:
            continue
        result[filename.removesuffix(".csv")] = [trace(stamps / 1000.0, values)]
    return result


def table(title: str, stats: dict) -> list[str]:
    lines = [f"### {title}", "", "```", report(stats), "```", ""]
    inside = sum(1 for key, _ in stats.items() if in_real_range(key, stats[key][0]))
    finite = sum(1 for key, _ in stats.items() if np.isfinite(stats[key][0]))
    lines.append(
        f"В диапазоне: {inside}/{finite} (определено {finite} из {len(SIGNATURES)})."
    )
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subjects", type=int, default=30)
    parser.add_argument("--real-dir", default=str(Path.home() / "Downloads/ukf_datasets"))
    parser.add_argument("--behaviour", action="store_true")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--isf-cv", type=float, default=0.0)
    parser.add_argument("--cr-cv", type=float, default=0.0)
    parser.add_argument("--readings-limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--compression-rate", type=float, default=0.0)
    parser.add_argument("--meal-plan", choices=("fixed", "template"), default="fixed")
    parser.add_argument("--variability", action="store_true")
    parser.add_argument("--title", default="Fidelity scorecard")
    parser.add_argument("--out", default="docs/acceptance/A26.md")
    parser.add_argument("--json", default="out/fidelity_scorecard.json")
    args = parser.parse_args(argv)
    if args.readings_limit and args.readings_limit < 408:
        parser.error("--readings-limit must be 0 or at least 408 (34 h at 5 min)")

    cgm, plasma = model_traces(
        load_subjects(), args.subjects, args.behaviour,
        days=args.days, isf_cv=args.isf_cv, cr_cv=args.cr_cv,
        readings_limit=args.readings_limit or None,
        workers=args.workers,
        compression_rate=args.compression_rate,
        meal_plan=args.meal_plan, variability=args.variability,
    )
    model = evaluate(cgm)
    model_plasma = evaluate(plasma)
    real = {name: evaluate(items) for name, items in real_traces(Path(args.real_dir)).items()}

    lines = [
        f"# {args.title}",
        "",
        "Дата: 2026-11-26",
        "Статус: **принят как scorecard**, не как валидация.",
        "",
        "## 1. Постановка",
        "",
        f"{args.subjects} субъектов, {args.days} сут FCL, CGM с residual-шумом; истинная плазменная глюкоза",
        "и CGM сравниваются с двенадцатью статистиками TimSim (MIT, `timsim/analysis/signatures.py`).",
        "Реальный конверт — 192 пользователя четырёх closed-loop систем; допуск ±10 % ширины",
        "диапазона. Реальные трейсы — Sibionics, Libre 2, AiDex из локального набора.",
        "",
        "## 2. Результаты",
        "",
    ]
    lines += table("CGM модели", model)
    lines += table("Истинная плазменная глюкоза модели", model_plasma)
    for name, stats in real.items():
        lines += table(f"Реальный трейс: {name}", stats)
    lines += [
        "## 3. Вывод",
        "",
        "1. Scorecard показывает, какие свойства распределения стенд воспроизводит, а какие нет;",
        "   это не доказательство валидности и не клинический вывод.",
        "2. Сравнение с реальными трейсами проверяет саму реализацию статистик: если реальный",
        "   трейс не попадает в конверт, проблема в реализации или в другом сенсоре/популяции.",
        "3. `drift` и `insulin_cv` определены только при ≥21 сутках и наличии истории доз;",
        "   `drift` — применённое расписание ISF, а не измерение человека.",
        "",
        "## 4. Ограничения",
        "",
        "1. 24 ч FCL, 30 базовых субъектов, один seed; без behaviour/site/M3.",
        "2. Конверт TimSim измерен на других сенсорах и популяции; совпадение/расхождение —",
        "   ориентир, не эквивалентность.",
        "3. Реальные трейсы локальны и не коммитятся; в отчёт попадают только агрегаты.",
        "4. Это не клинический вывод.",
        "",
    ]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    Path(args.json).write_text(json.dumps({
        "model_cgm": model, "model_plasma": model_plasma, "real": real,
        "subjects": args.subjects, "behaviour": args.behaviour,
        "days": args.days, "isf_cv": args.isf_cv, "cr_cv": args.cr_cv,
        "readings_limit": args.readings_limit or None, "workers": args.workers,
        "compression_rate": args.compression_rate,
        "meal_plan": args.meal_plan, "variability": args.variability,
    }, indent=2), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
