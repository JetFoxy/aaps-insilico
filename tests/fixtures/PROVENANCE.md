# Provenance

## `cgm_residuals.npy`

CGM noise for the closed-loop runs. Each value is one reading minus a local Savitzky-Golay trend
(window 7, polyorder 2) inside one continuous sensor segment; segments are split at gaps longer
than 20 minutes. 11 422 values from a Libre sensor, 5-minute cadence.

What is **not** stored: timestamps, glucose levels, sensor serial numbers, device identifiers.
A residual is a noise sample, not a glucose trace, and the values alone do not reconstruct either
the trend or the readings. The file is shipped so that runs are reproducible without the original
database.

The generating code is `python/cgm/residuals.py::residuals_from_db`; the source database is a
personal xDrip export and is never committed (`data/raw/` is gitignored).

Statistics (used by M4): sigma 2.49 mg/dL, PSD slope 3.72 over 0.002–0.08 cycles/min.
