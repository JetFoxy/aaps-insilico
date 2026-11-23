#!/usr/bin/env python3
"""W2 parity check: replay decisions against recorded `openaps.suggested`."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.client import Bridge, BridgeError, insulin_from_settings, load_prefs
from nightscout.trace import DayTrace, DecisionRecord, trace_from_dict
from runner.replay_loop import (
    ReplayCycle,
    ReplayResult,
    ReplayVariant,
    replay_from_dict,
    replay_to_dict,
    run_replay,
)

ROOT = Path(__file__).resolve().parents[2]
MATCH_WINDOW_MS = 60_000
MAIN_FIELDS = ("final_isf", "insulin_req", "rate", "smb_on")

DEFAULT_TOLERANCES = {
    "final_isf": 0.01,
    "insulin_req": 0.05,
    "rate": 0.05,
    "smb_on": 0.0,
    "factor": 0.01,
}

_COUNT_WARNINGS = (
    ("excluded loop boluses", re.compile(r"^excluded loop boluses: (\d+)$")),
    ("unclassified boluses", re.compile(r"^unclassified boluses: (\d+)$")),
    ("cycles without a decision", re.compile(r"^cycle at \d+ returned no decision$")),
)


def _cycle_final_isf(cycle: ReplayCycle) -> float | None:
    """AAPS records the autoISF multiplier, so prefer the bridge's matching factor."""
    factor = cycle.factors.get("finalIsf")
    return cycle.final_isf if factor is None else factor


def _pair_cycles(
    replay: ReplayResult, decisions: list[DecisionRecord]
) -> tuple[list[tuple[DecisionRecord, ReplayCycle]], int]:
    pairs: list[tuple[DecisionRecord, ReplayCycle]] = []
    unmatched = 0
    for record in decisions:
        best: tuple[int, int] | None = None
        for index, cycle in enumerate(replay.cycles):
            delta = abs(cycle.ts - record.deliver_at)
            if delta <= MATCH_WINDOW_MS and (best is None or delta < best[0]):
                best = (delta, index)
        if best is None:
            unmatched += 1
            continue
        pairs.append((record, replay.cycles[best[1]]))
    return pairs, unmatched


def _numeric(
    stats: dict[str, dict], name: str, tolerance: float, expected, actual
) -> None:
    row = stats[name]
    row["n"] += 1
    if expected is None and actual is None:
        row["matched"] += 1
        row["match"] = row["matched"] / row["n"]
        return
    if expected is None or actual is None:
        row["match"] = row["matched"] / row["n"]
        return
    error = abs(float(expected) - float(actual))
    if row["max_error"] is None or error > row["max_error"]:
        row["max_error"] = round(error, 6)
    if error <= tolerance:
        row["matched"] += 1
    row["match"] = row["matched"] / row["n"]


def compare(
    replay: ReplayResult,
    decisions: list[DecisionRecord],
    tolerances: dict[str, float] = DEFAULT_TOLERANCES,
) -> dict[str, dict]:
    """Match replay cycles to recorded decisions and score every field."""
    pairs, unmatched = _pair_cycles(replay, decisions)
    factor_names = sorted(
        {str(name) for record in decisions for name in record.factors}
    )
    names = [*MAIN_FIELDS, *(f"factor:{name}" for name in factor_names)]
    stats: dict[str, dict] = {
        name: {"n": 0, "matched": 0, "match": 0.0, "max_error": None}
        for name in names
    }
    for record, cycle in pairs:
        _numeric(
            stats,
            "final_isf",
            tolerances["final_isf"],
            record.final_isf,
            _cycle_final_isf(cycle),
        )
        _numeric(
            stats,
            "insulin_req",
            tolerances["insulin_req"],
            record.insulin_req,
            cycle.insulin_req,
        )
        _numeric(stats, "rate", tolerances["rate"], record.rate, cycle.rate)
        _numeric(
            stats,
            "smb_on",
            tolerances["smb_on"],
            float(record.smb_on),
            float(cycle.smb > 0),
        )
        for name in factor_names:
            _numeric(
                stats,
                f"factor:{name}",
                tolerances["factor"],
                record.factors.get(name),
                cycle.factors.get(name),
            )
    stats["unmatched"] = {"n": unmatched, "matched": 0, "match": 0.0, "max_error": 0.0}
    stats["skipped"] = {
        "n": len(replay.skipped),
        "matched": 0,
        "match": 0.0,
        "max_error": 0.0,
    }
    return stats


def _warning_counts(warnings: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, pattern in _COUNT_WARNINGS:
        for warning in warnings:
            if label == "cycles without a decision":
                if pattern.match(warning):
                    counts[label] = counts.get(label, 0) + 1
                continue
            found = pattern.match(warning)
            if found:
                counts[label] = int(found.group(1))
    return counts


def _coverage(stats: dict[str, dict], decisions: list[DecisionRecord]) -> float:
    if not decisions:
        return 0.0
    return stats["final_isf"]["n"] / len(decisions)


def _verdict(stats: dict[str, dict], decisions: list[DecisionRecord]) -> tuple[bool, str]:
    coverage = _coverage(stats, decisions)
    failures = [name for name in MAIN_FIELDS if stats[name]["match"] < 1.0]
    parts: list[str] = []
    if coverage < 0.95:
        parts.append(f"покрытие {coverage:.1%}")
    if failures:
        parts.append(
            "; ".join(
                f"{name} {stats[name]['matched']}/{stats[name]['n']}"
                for name in failures
            )
        )
    if not parts:
        return True, ""
    return False, "; ".join(parts)


def _format_error(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.4f}"


def _format_tolerance(name: str, tolerance: float) -> str:
    return "точно" if name == "smb_on" or tolerance == 0.0 else f"{tolerance:g}"


def _render(
    trace: DayTrace,
    decisions: list[DecisionRecord],
    replay: ReplayResult,
    stats: dict[str, dict],
    tolerances: dict[str, float],
) -> str:
    passed, reason = _verdict(stats, decisions)
    counts = _warning_counts(replay.warnings)
    rows = [name for name in stats if name not in ("unmatched", "skipped")]
    lines = [
        "# W2 — сверка replay с `openaps.suggested`",
        "",
        "Дата: 2026-11-23",
        f"Статус: **{'принят' if passed else 'не принят'}**"
        + (f" — {reason}" if reason else "")
        + ".",
        "",
        "## 1. Постановка",
        "",
        "По спеке §4.4: baseline replay должен воспроизводить записанные решения "
        "`openaps.suggested` по `devicestatus` того же дня. Сверяются `finalIsf`, `insulinReq`, "
        "`rate`, `smbOn` и факторы `autoIsf`; допуски зафиксированы до первого прогона и в ходе "
        "приёмки не менялись.",
        "",
        "## 2. Метод сверки",
        "",
        "1. Трейс импортируется из Nightscout (W1): сырые точки CGM, лечения, профиль, "
        "записи решений.",
        "2. Baseline-вариант собирается из настроек стенда и профиля Nightscout; replay идёт "
        "по 5-минутной сетке, глюкоза не пересчитывается.",
        "3. Каждое записанное решение сопоставляется с ближайшим циклом replay по `deliverAt` "
        "с окном ±1 мин; без пары решение попадает в `unmatched`.",
        "4. Для каждого поля считается доля совпадений в пределах допуска и максимальная "
        "абсолютная ошибка; `smbOn` сравнивается с фактом доставки SMB (`smb > 0`).",
        "5. AAPS пишет в `openaps.suggested.autoIsf.finalIsf` итоговый множитель autoISF, а не "
        "абсолютную чувствительность; поэтому поле сверяется с множителем `aiv.finalIsf` из "
        "ответа моста, а не с `sens` (mg/dL/U).",
        "",
        "## 3. Результаты",
        "",
        "| Поле | Совпадение | Макс. ошибка | Порог |",
        "|---|---|---|---|",
    ]
    for name in rows:
        row = stats[name]
        tolerance = tolerances[name.split(":")[0] if name.startswith("factor:") else name]
        lines.append(
            f"| `{name}` | {row['matched']}/{row['n']} ({row['match']:.1%}) | "
            f"{_format_error(row['max_error'])} | {_format_tolerance(name, tolerance)} |"
        )
    lines += [
        "",
        "## 4. Исключённые циклы",
        "",
        f"- сопоставлено пар «решение — цикл»: {stats['final_isf']['n']} из {len(decisions)};",
        f"- без пары по времени (`unmatched`): {stats['unmatched']['n']};",
        f"- пропущено из-за пропуска CGM (`skipped`): {stats['skipped']['n']};",
        f"- исключено loop-болюсов (SMB из записи): {counts.get('excluded loop boluses', 0)};",
        f"- неклассифицированных болюсов: {counts.get('unclassified boluses', 0)};",
        f"- циклов без решения моста: {counts.get('cycles without a decision', 0)}.",
        "",
        "## 5. Вывод",
        "",
    ]
    if passed:
        lines.append(
            "Baseline replay воспроизводит записанные решения в пределах допусков на всех "
            "сопоставленных циклах; W2 принят."
        )
    else:
        coverage = _coverage(stats, decisions)
        failed = [name for name in MAIN_FIELDS if stats[name]["match"] < 1.0]
        lines.append("W2 не принят. Допуски не подкручивались, результат зафиксирован как есть.")
        lines.append("")
        if coverage < 0.95:
            lines.append(
                f"- Покрытие по `deliverAt` — {stats['final_isf']['n']} из {len(decisions)} "
                f"({coverage:.1%}) при требуемых 95%: записанные решения идут чаще, чем "
                "5-минутная сетка replay, поэтому у большей части решений нет своего цикла. "
                "Это ограничение шага replay, а не допусков."
            )
        if failed:
            detail = ", ".join(
                f"`{name}` {stats[name]['matched']}/{stats[name]['n']} "
                f"({stats[name]['match']:.1%})"
                for name in failed
            )
            lines.append(f"- Даже на сопоставленных циклах поля расходятся: {detail}.")
        weak_factors = [
            f"`{name}` {stats[name]['match']:.1%}"
            for name in stats
            if name.startswith("factor:") and stats[name]["match"] < 0.5
        ]
        if weak_factors:
            lines.append(
                "- Из факторов ниже половины совпадений: " + ", ".join(weak_factors) + "."
            )
    lines += [
        "",
        "## 6. Ограничения",
        "",
        "1. Проверяется только baseline-вариант; контрфактические варианты W2 не покрывает.",
        "2. Глюкоза в replay — реальный CGM, поэтому паритет характеризует решения, а не "
        "исходы.",
        "3. Циклы с пропуском CGM и без пары по `deliverAt` исключены из процента и "
        "перечислены в §4.",
        "4. Temp target реконструируется из лечений; exercise и неизвестные события не "
        "воспроизводятся.",
        "5. Смена профиля применяется только через сегменты профиля Nightscout.",
        "6. Числа в отчёте агрегированные: значения глюкозы, время приёмов пищи и параметры "
        "инсулина не публикуются.",
        "7. Шаг replay фиксирован (5 мин), а записанные решения приходят чаще; выравнивание "
        "сетки по `deliverAt` — следующий шаг, без него покрытие W2 не достигает 95%.",
        "",
    ]
    return "\n".join(lines)


def _build_result(args: argparse.Namespace, trace: DayTrace) -> ReplayResult:
    if args.replay:
        return replay_from_dict(json.loads(Path(args.replay).read_text(encoding="utf-8")))
    prefs_path = Path(args.prefs)
    prefs = load_prefs(prefs_path)
    insulin = insulin_from_settings(prefs_path, args.insulin)
    with Bridge() as bridge:
        return run_replay(
            trace,
            prefs,
            insulin,
            ReplayVariant(label="baseline"),
            bridge,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="check_replay_parity")
    parser.add_argument("--trace", required=True)
    parser.add_argument("--prefs")
    parser.add_argument("--insulin")
    parser.add_argument("--replay")
    parser.add_argument("--out")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.replay and not (args.prefs and args.insulin):
        print("parity check failed: --prefs and --insulin are required without --replay",
              file=sys.stderr)
        return 1
    try:
        trace = trace_from_dict(json.loads(Path(args.trace).read_text(encoding="utf-8")))
        replay = _build_result(args, trace)
    except (OSError, KeyError, ValueError, BridgeError) as exc:
        print(f"parity check failed: {exc}", file=sys.stderr)
        return 1
    decisions = trace.decisions
    stats = compare(replay, decisions)
    passed, reason = _verdict(stats, decisions)
    report = _render(trace, decisions, replay, stats, DEFAULT_TOLERANCES)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
    replay_path = ROOT / "out" / "whatif" / "replay.json"
    replay_path.parent.mkdir(parents=True, exist_ok=True)
    replay_path.write_text(
        json.dumps(replay_to_dict(replay), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "decisions": len(trace.decisions),
        "matched": stats["final_isf"]["n"],
        "unmatched": stats["unmatched"]["n"],
        "skipped": stats["skipped"]["n"],
        **{name: stats[name]["match"] for name in MAIN_FIELDS},
        "passed": passed,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
