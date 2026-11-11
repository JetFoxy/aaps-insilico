"""Loader for the public MetaboNet parquet (real CGM + insulin + meals, multi-device).

The file is ~1.4 GB and lives outside the repository. It is a public research dataset, but it is
still real patient data: never commit it. This module reads only the columns it needs and only the
row groups that contain the requested subject.

Schema used: id, date, CGM (mg/dL), cgm_device, basal, bolus, carbs, insulin.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["id", "date", "CGM", "cgm_device"]


def load_trace(path: Path | str, subject_id: str, max_row_groups: int = 0) -> tuple[np.ndarray, np.ndarray, str]:
    """Return (timestamp_ms, CGM mg/dL, device) for one subject."""
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(str(path))
    groups = parquet.num_row_groups if max_row_groups <= 0 else min(max_row_groups, parquet.num_row_groups)
    frames = []
    device = "unknown"
    for index in range(groups):
        frame = parquet.read_row_group(index, columns=COLUMNS).to_pandas()
        subset = frame[frame["id"].astype(str) == str(subject_id)]
        if not subset.empty:
            frames.append(subset)
            device = str(subset["cgm_device"].iloc[0])
            if sum(len(item) for item in frames) >= 20_000:
                break
    if not frames:
        raise KeyError(f"subject {subject_id!r} not found in the first {groups} row groups")
    data = pd.concat(frames).dropna(subset=["CGM"]).sort_values("date")
    timestamps = data["date"].astype("int64").to_numpy() // 1_000_000
    values = data["CGM"].to_numpy(dtype=float)
    return timestamps, values, device


def subject_ids(path: Path | str, max_row_groups: int = 1) -> list[str]:
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(str(path))
    frame = parquet.read_row_group(0, columns=["id"]).to_pandas()
    return sorted(frame["id"].astype(str).unique())
