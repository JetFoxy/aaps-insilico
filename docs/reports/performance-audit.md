# Performance audit — 2026-11-11

Baseline selection: `adolescent#001` × `fcl-24h` and `announced-8h` × autoISF off/on (planned temps, residual CGM noise). Wall time was 9.35 s before and 6.87 s after; all listed A2 metrics were unchanged.

The bridge `cycle` RPC replaces the per-cycle glucose, IOB, autoISF/DynISF, meal, and step calls while preserving their internal order. On the selected 24-hour fcl arms, RPC count fell from baseline totals of 861 (off) / 1147 (on), including init, to 288 cycle calls per task, or 289 including init; an announced 8-hour task fell from baseline totals of 379 (off) / 473 (on), including init, to 96 cycle calls, or 97 including init. The speedup comes from reducing JSON-RPC round trips; profile cache keys include subject name, all parameters, initial state, and calculation options.

`pipeline.glucose()` remains stateful and mutates the store and UKF, so no glucose result cache is used. The batch does not redirect the second pipeline call made inside autoISF/DynISF: in the original command sequence that call has UKF-learning side effects, and substituting another status changed algorithm trajectories. Keeping that second call preserves the golden behavior.

LSODA is available through `simulate(..., method=...)` but BDF remains the default and production method. A three-group (`adult#001`, `adolescent#001`, `child#005`) 180-minute post-meal comparison had max deltas glucose 0.000526 mg/dL, plasma insulin 0.005153 pmol/L, and subcutaneous insulin 0.004939 pmol/L, below the 0.01 golden tolerance. Longer 480-minute meal and exercise LSODA runs did not complete within the practical benchmark window because LSODA repeatedly reduces its step across discontinuous meal, bolus, exercise, and risk terms; therefore LSODA is not enabled.
