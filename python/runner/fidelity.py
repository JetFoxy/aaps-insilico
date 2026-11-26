"""Twelve signatures that distinguish a real trace from a simulated one.

The definitions and the real-world ranges follow TimSim (MIT, ``timsim/analysis/signatures.py``),
which measured the ranges across 192 users of four closed-loop systems. Ten signatures are
computed identically on real and simulated glucose traces on a five-minute grid. ``insulin_cv``
needs the pump's delivery log and ``drift`` needs the sensitivity the algorithm was given, so
both are carried alongside the trace and are undefined when a trace has no such series.

``drift`` is not evidence about the person: TimSim measured that its controller-derived value
does not distinguish a subject whose sensitivity varies from one whose does not. It is reported
for continuity and labelled with that limitation.
"""
from __future__ import annotations

import numpy as np

REAL_RANGE = {
    "cv": (29.5, 34.3),
    "tail": (3.7, 6.6),
    "acf30": (0.78, 0.87),
    "acf60": (0.50, 0.68),
    "outcome": (26.5, 33.5),
    "diurnal": (34.7, 56.3),
    "hypo_rec": (50.0, 59.0),
    "hypo_reb": (23.2, 28.4),
    "compress": (1.9, 5.3),
    "noise": (4.5, 6.7),
    "drift": (8.2, 21.7),
    "insulin_cv": (4.5, 11.1),
}


def trace(t_seconds, bg, cadence: float = 300.0) -> dict:
    t = np.asarray(t_seconds, dtype=float)
    values = np.asarray(bg, dtype=float)
    order = np.argsort(t)
    t, values = t[order], values[order]
    grid = np.arange(t[0], t[-1] + 1.0, cadence)
    sampled = np.interp(grid, t, values)
    return {"t": grid, "bg": sampled, "hour": (grid / 3600.0) % 24.0, "cadence": cadence}


def from_loop(loop, plasma: bool = False, cadence: float = 300.0) -> dict:
    values = loop.glucose if plasma else loop.cgm
    item = trace(np.asarray(loop.time, dtype=float) * 60.0, values, cadence)
    if getattr(loop, "dose_events", None):
        item["insulin"] = insulin_from_events(loop.dose_events, len(values), cadence / 60.0)
    if getattr(loop, "variable_sens", None):
        item["sens"] = np.asarray(loop.variable_sens, dtype=float)
    return item


def _deltas(d, lo=240.0, hi=360.0):
    dt, dbg = np.diff(d["t"]), np.diff(d["bg"])
    return dbg[(dt >= lo) & (dt <= hi)]


def _lag_corr(t, bg, lag_s, tol=90.0):
    j = np.searchsorted(t, t + lag_s)
    j = np.clip(j, 0, len(t) - 1)
    ok = np.abs(t[j] - (t + lag_s)) <= tol
    if ok.sum() < 50:
        return np.nan
    x, y = bg[ok], bg[j][ok]
    xm, ym = x.mean(), y.mean()
    denom = np.sqrt(((x - xm) ** 2).sum() * ((y - ym) ** 2).sum())
    return float(((x - xm) * (y - ym)).sum() / denom) if denom else np.nan


def _outcome(d, lo=180.0, hi=240.0, horizon=1800.0, tol=240.0, minn=200):
    t, bg = d["t"], d["bg"]
    j = np.clip(np.searchsorted(t, t + horizon), 0, len(t) - 1)
    good = np.abs(t[j] - (t + horizon)) <= tol
    inband = (bg >= lo) & (bg < hi) & good
    if inband.sum() < minn:
        return np.nan
    return float((bg[j][inband] - bg[inband]).std())


def _diurnal(d):
    h, bg = d["hour"], d["bg"]
    prof = np.array([
        bg[(h >= k) & (h < k + 1)].mean() if np.any((h >= k) & (h < k + 1)) else np.nan
        for k in range(24)
    ])
    prof = prof[np.isfinite(prof)]
    return float(prof.max() - prof.min()) if len(prof) else np.nan


def _hypo(d):
    t, bg = d["t"], d["bg"]
    below = bg < 70.0
    onset = np.where(below[1:] & ~below[:-1])[0] + 1
    rec, reb = [], []
    for i in onset:
        end = min(i + 40, len(bg) - 1)
        st, sb = t[i:end + 1], bg[i:end + 1]
        r = np.where(sb >= 100.0)[0]
        if not len(r):
            continue
        rec.append((st[r[0]] - st[0]) / 60.0)
        r2 = min(r[0] + 25, len(sb) - 1)
        reb.append(bool(np.any(sb[r[0]:r2 + 1] > 180.0)))
    if not rec:
        return np.nan, np.nan
    return float(np.median(rec)), float(np.mean(reb))


def _compression(d):
    t, bg = d["t"], d["bg"]
    below = bg < 70.0
    onset = np.where(below[1:] & ~below[:-1])[0] + 1
    count = 0
    for i in onset:
        pre = bg[max(0, i - 4):i].mean() if i >= 1 else bg[i]
        w = min(i + 6, len(bg) - 1)
        seg = bg[i:w + 1]
        nadir = seg.min()
        if seg[-1] >= pre - 15.0 and pre >= 85.0 and (pre - nadir) > 25.0:
            count += 1
    span = (t[-1] - t[0]) / 86400.0 if len(t) > 1 else 1.0
    return 30.0 * count / max(span, 1.0)


def _noise(d, lo=240.0, hi=360.0):
    t, bg = d["t"], d["bg"]
    d2 = np.diff(np.diff(bg))
    g = np.diff(t)
    ok = (g[:-1] >= lo) & (g[:-1] <= hi) & (g[1:] >= lo) & (g[1:] <= hi)
    return float(np.std(d2[ok])) if np.any(ok) else np.nan


def insulin_from_events(dose_events, n_cycles: int, cadence_min: float = 5.0) -> np.ndarray:
    """Units the pump pushed in each cycle, from the loop's dose log."""
    series = np.zeros(max(0, int(n_cycles)), dtype=float)
    if cadence_min <= 0 or series.size == 0:
        return series
    for time_min, units, _kind in dose_events:
        index = int(round(float(time_min) / cadence_min)) - 1
        if 0 <= index < series.size:
            series[index] += float(units)
    return series


def insulin_weekly_cv(insulin_per_step, step_minutes: float = 5.0) -> float:
    """Week-to-week %CV of total daily insulin, on the pump's delivery log.

    The arithmetic is TimSim's: a rolling 24-hour total at one value per step, reduced to one
    value per day by its median, averaged into weeks, and expressed as a CV across weeks.
    It needs at least three weeks, so a shorter run returns NaN rather than a misleading number.
    """
    values = np.asarray(insulin_per_step, dtype=float)
    if values.size < 2 or step_minutes <= 0:
        return float("nan")
    per_day = int(round(1440.0 / float(step_minutes)))
    if per_day < 1 or values.size < 3 * per_day:
        return float("nan")
    rolling = np.convolve(values, np.ones(per_day), mode="valid")
    day_of = np.arange(rolling.size) // per_day
    daily = np.array([np.median(rolling[day_of == day]) for day in np.unique(day_of)])
    if daily.size < 14:
        return float("nan")
    week_of = np.arange(daily.size) // 7
    weekly = np.array([daily[week_of == week].mean() for week in np.unique(week_of)])
    if weekly.size < 3 or weekly.mean() <= 0:
        return float("nan")
    return float(100.0 * weekly.std() / weekly.mean())


def weekly_sens_cv(sens_per_step, step_minutes: float = 5.0) -> float:
    """Week-to-week %CV of the sensitivity the algorithm was given."""
    values = np.asarray(sens_per_step, dtype=float)
    if values.size < 2 or step_minutes <= 0:
        return float("nan")
    per_day = int(round(1440.0 / float(step_minutes)))
    if per_day < 1:
        return float("nan")
    daily = np.array([
        np.median(values[index * per_day:(index + 1) * per_day])
        for index in range(values.size // per_day)
    ])
    if daily.size < 14:
        return float("nan")
    week_of = np.arange(daily.size) // 7
    weekly = np.array([daily[week_of == week].mean() for week in np.unique(week_of)])
    if weekly.size < 3 or weekly.mean() <= 0:
        return float("nan")
    return float(100.0 * weekly.std() / weekly.mean())


def _drift(d):
    if "sens" not in d:
        return float("nan")
    return weekly_sens_cv(d["sens"], d.get("cadence", 300.0) / 60.0)


def _insulin_cv(d):
    if "insulin" not in d:
        return float("nan")
    return insulin_weekly_cv(d["insulin"], d.get("cadence", 300.0) / 60.0)


SIGNATURES = (
    ("cv", lambda d: 100.0 * d["bg"].std() / d["bg"].mean()),
    ("tail", lambda d: 100.0 * np.mean(_deltas(d) > 10.0) if len(_deltas(d)) else np.nan),
    ("acf30", lambda d: _lag_corr(d["t"], d["bg"], 1800.0)),
    ("acf60", lambda d: _lag_corr(d["t"], d["bg"], 3600.0)),
    ("outcome", _outcome),
    ("diurnal", _diurnal),
    ("hypo_rec", lambda d: _hypo(d)[0]),
    ("hypo_reb", lambda d: 100.0 * _hypo(d)[1] if np.isfinite(_hypo(d)[1]) else np.nan),
    ("compress", _compression),
    ("noise", _noise),
    ("drift", _drift),
    ("insulin_cv", _insulin_cv),
)


def boot_ci(values, n: int = 2000, seed: int = 0):
    values = np.asarray([v for v in values if np.isfinite(v)], dtype=float)
    if values.size == 0:
        return (float("nan"), float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    index = rng.integers(0, values.size, size=(n, values.size))
    samples = np.array([np.median(values[i]) for i in index])
    return float(np.median(values)), float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))


def evaluate(traces, seed: int = 0) -> dict:
    return {
        key: boot_ci([fn(item) for item in traces], seed=seed + index)
        for index, (key, fn) in enumerate(SIGNATURES)
    }


def in_real_range(key: str, value: float, pad: float = 0.10) -> bool:
    lo, hi = REAL_RANGE[key]
    span = hi - lo
    return bool(lo - pad * span - 1e-9 <= value <= hi + pad * span + 1e-9)


def report(stats: dict, pad: float = 0.10) -> str:
    lines = [f"{'signature':<10} {'real range':>14} {'measured':>24}  verdict"]
    for key, _ in SIGNATURES:
        if key not in stats:
            continue
        lo, hi = REAL_RANGE[key]
        value, low, high = stats[key]
        if not np.isfinite(value):
            ok = "n/a"
        else:
            ok = "in range" if in_real_range(key, value, pad) else "OUT"
        lines.append(f"{key:<10} {lo:6.1f}-{hi:<7.1f} {value:8.2f} [{low:6.2f}-{high:6.2f}]  {ok}")
    return "\n".join(lines)
