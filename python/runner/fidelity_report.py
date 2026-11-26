"""Fidelity scorecard: ten glucose signatures of the stand against TimSim's real ranges."""
from __future__ import annotations

import argparse
import json
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from bridge.client import Bridge, insulin_from_settings, load_prefs
from cgm.validate_sensors import load_trace
from runner.closed_loop import AlgorithmProfile, Scenario, init_bridge, run_closed_loop
from runner.fidelity import evaluate, from_loop, in_real_range, report, trace
from runner.options import RESIDUALS
from simcore.params import load_subjects
from simcore.population import compute_cf, compute_cr, compute_tdi

MEALS = [(7 * 60, 50.0), (13 * 60, 50.0), (19 * 60, 50.0)]
PREFS = load_prefs(ROOT / "data/raw/settings.json")
INSULIN = insulin_from_settings(ROOT / "data/raw/settings.json", "Lyumjev 45m 8h 100 ед")
REAL_FILES = ("sibionics2.csv", "libre2.csv", "aidex.csv")


def profile_for(subject) -> AlgorithmProfile:
    cr = compute_cr(subject) or 10.5
    tdi = compute_tdi(subject, cr) or 40.0
    return AlgorithmProfile(
        sens=compute_cf(tdi) or 50.0, carb_ratio=cr, target_bg=110.0,
        current_basal=subject.basal_u_per_min * 60, enable_auto_isf=False,
    )


def model_traces(subjects: dict, count: int) -> tuple[list[dict], list[dict]]:
    cgm, plasma = [], []
    with Bridge() as bridge:
        for name in [key for key in sorted(subjects)][:count]:
            subject = subjects[name]
            profile = profile_for(subject)
            scenario = Scenario(duration_min=24 * 60, meals=MEALS)
            init_bridge(bridge, subject, PREFS, INSULIN, profile, scenario)
            loop = run_closed_loop(
                subject, bridge, profile, scenario,
                seed=zlib.crc32(name.encode()) & 0xFFFF,
                residuals=RESIDUALS, noise_kind="residuals",
            )
            cgm.append(from_loop(loop))
            plasma.append(from_loop(loop, plasma=True))
    return cgm, plasma


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
    lines.append(f"В диапазоне: {inside}/{finite} (всего подписей 10).")
    lines.append("")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--subjects", type=int, default=30)
    parser.add_argument("--real-dir", default=str(Path.home() / "Downloads/ukf_datasets"))
    parser.add_argument("--out", default="docs/acceptance/A26.md")
    parser.add_argument("--json", default="out/fidelity_scorecard.json")
    args = parser.parse_args(argv)

    cgm, plasma = model_traces(load_subjects(), args.subjects)
    model = evaluate(cgm)
    model_plasma = evaluate(plasma)
    real = {name: evaluate(items) for name, items in real_traces(Path(args.real_dir)).items()}

    lines = [
        "# A26 — fidelity scorecard: стенд против реального конверта",
        "",
        "Дата: 2026-11-26",
        "Статус: **принят как scorecard**, не как валидация.",
        "",
        "## 1. Постановка",
        "",
        f"{args.subjects} субъектов, 24 ч FCL, CGM с residual-шумом; истинная плазменная глюкоза",
        "и CGM сравниваются с десятью статистиками TimSim (MIT, `timsim/analysis/signatures.py`).",
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
        "3. `drift` и `insulin_cv` здесь не считаются: нужны многодневные прогоны и история доз.",
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
        "subjects": args.subjects,
    }, indent=2), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
