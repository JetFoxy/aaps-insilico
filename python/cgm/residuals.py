"""Real CGM residuals from the xDrip database.

The bench does not synthesise a "similar" noise model: it replays blocks of real residuals.
A residual is the deviation of a stored reading from a local Savitzky-Golay trend inside one
continuous sensor segment. Segments are split at gaps longer than 20 minutes; sessions are never
concatenated (see docs/ukf-5227-adaptive-directions.md, correction 2026-10-22).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import savgol_filter

GAP_MIN = 20.0
SG_WINDOW = 7
SG_POLY = 2


@dataclass
class ResidualSet:
    values: np.ndarray
    segments: int
    sigma: float
    lag1: float

    def summary(self) -> dict:
        return {
            "n": int(self.values.size),
            "segments": self.segments,
            "sigma": float(self.sigma),
            "lag1": float(self.lag1),
        }


def _segments(timestamps: np.ndarray, values: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    if timestamps.size == 0:
        return []
    splits = np.where(np.diff(timestamps) > GAP_MIN * 60_000)[0] + 1
    parts = []
    for start, end in zip(np.r_[0, splits], np.r_[splits, timestamps.size]):
        if end - start >= SG_WINDOW:
            parts.append((timestamps[start:end], values[start:end]))
    return parts


def residuals_from_db(db_path: Path | str, column: str = "raw_data", start_ms: int | None = None, end_ms: int | None = None) -> ResidualSet:
    """Residuals of `column` against a local SG trend, one segment per continuous sensor run."""
    con = sqlite3.connect(Path(db_path))
    query = f"select timestamp, {column} from BgReadings where {column} is not null and {column} > 0"
    params: list[int] = []
    if start_ms is not None:
        query += " and timestamp >= ?"
        params.append(start_ms)
    if end_ms is not None:
        query += " and timestamp <= ?"
        params.append(end_ms)
    query += " order by timestamp"
    rows = con.execute(query, params).fetchall()
    timestamps = np.array([r[0] for r in rows], dtype=np.int64)
    values = np.array([r[1] for r in rows], dtype=float)

    out = []
    segments = 0
    for segment_ts, segment_values in _segments(timestamps, values):
        trend = savgol_filter(segment_values, SG_WINDOW, SG_POLY, mode="interp")
        out.append(segment_values - trend)
        segments += 1
    residuals = np.concatenate(out) if out else np.array([])
    sigma = float(residuals.std(ddof=1)) if residuals.size > 1 else 0.0
    lag1 = float(np.corrcoef(residuals[:-1], residuals[1:])[0, 1]) if residuals.size > 2 else 0.0
    return ResidualSet(values=residuals, segments=segments, sigma=sigma, lag1=lag1)


def psd_slope(signal: np.ndarray, fs: float, f_min: float = 0.002, f_max: float = 0.08) -> float:
    """Log-log slope of the Welch PSD over [f_min, f_max], in cycles per minute."""
    from scipy.signal import welch

    nper = int(min(len(signal), max(256, 2 ** int(np.log2(len(signal) / 4)))))
    frequencies, power = welch(signal, fs=fs, nperseg=nper)
    mask = (frequencies >= f_min) & (frequencies <= f_max) & (power > 0)
    if mask.sum() < 4:
        return float("nan")
    return float(np.polyfit(np.log10(frequencies[mask]), np.log10(power[mask]), 1)[0])
