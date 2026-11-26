# Multi-day drift pilot (A28)

Spec: `docs/superpowers/specs/2026-11-10-insilico-stand-design.md`
Baseline: `main` at `23b3bd6`

## Goal

Extend the fidelity scorecard to runs long enough for the two remaining TimSim signatures —
`insulin_cv` (week-to-week CV of total daily insulin) and `drift` (week-to-week CV of the
sensitivity setting) — and for a meaningful `diurnal`/`outcome`. Exercise the existing
`adaptive_profile_schedule` feed on a multi-day closed loop, then report what the extra days
change.

## Global constraints

- Kotlin AAPS stays the only algorithm; no Python port.
- Every threshold or parameter keeps its source; `drift` is not evidence about the person
  (TimSim docstring) and must be labelled as such.
- No personal data; real traces only as aggregates.
- TDD for every production change; full suite before each commit.
- Deterministic by seed.

## Task 1 — insulin and drift signatures

Files: `python/runner/fidelity.py`, `tests/test_fidelity.py`

Steps:

1. RED: tests for `insulin_weekly_cv` (constant → 0; week-level shift → expected CV; short → NaN),
   `weekly_sens_cv`, `insulin_from_events` (basal per cycle plus one bolus), and `evaluate`
   returning both keys with NaN when the trace carries no insulin/sens.
   Expected: `ImportError`/`AttributeError`, 0 passed.
2. GREEN: port TimSim's `insulin_weekly_cv` arithmetic (rolling 24 h, daily median, weekly
   mean, CV across weeks, ≥3 weeks), add `weekly_sens_cv`, `insulin_from_events`, extend
   `trace()`/`from_loop()` to carry `insulin` and `sens`, extend `REAL_RANGE`/`SIGNATURES`,
   make `report()` print `n/a` for non-finite values.
   Expected: task tests pass.
3. Full suite green; commit `feat(fidelity): insulin and drift signatures`.

## Task 2 — multi-day runner

Files: `python/runner/fidelity_report.py`, `python/runner/closed_loop.py`, `tests/test_behaviour.py`

Steps:

1. RED: tests for `meals_for_days` (meal times repeated per day, count = days × meals) and
   for `run_closed_loop(..., readings_limit=N)` passing at most N readings to the bridge
   (assert on a fake bridge's received payload).
   Expected: failures for missing helpers.
2. GREEN: add `meals_for_days`, `--days`, `--isf-cv`/`--cr-cv`, `--readings-limit`, `--workers`; build the
   scenario for `days × 1440` minutes; call `adaptive_profile_schedule` for runs ≥ 7 days and
   pass it to the loop; carry `loop.dose_events`/`variable_sens` into the trace; cap the
   bridge readings list when asked.
   Expected: task tests pass.
3. Full suite green; commit `feat(fidelity): multi-day runs with profile drift`.

## Task 3 — pilot and A28

Steps:

1. Smoke: one subject × 22 days, print per-signature values and wall time.
2. Pilots: 30 subjects × 22 days, `isf_cv=0` and `isf_cv=0.15` (TimSim's measured 8-22 % weekly
   ISF drift, mid-range), exit-code checked; JSON in `out/`. 22 days is the first run with three
   complete weeks after the rolling 24 h window.
3. Write `docs/acceptance/A28.md`: 12-signature table (model vs real envelope), which signatures
   became defined, what is the imposed knob and what is the model, limitations.
4. Update design table + README; commit `feat(fidelity): multi-day drift pilot; A28`.

## Review Focus

- The readings cap must not change ≤48 h runs (default uncapped).
- `drift` is the applied ISF schedule, not a measurement of the person.
- `insulin_weekly_cv` needs three complete weeks: 21 days for `weekly_sens_cv`, 22 days for
  `insulin_weekly_cv` (the rolling window consumes one day); a shorter run must be `n/a`.
- `isf_cv=0.15` must be presented as the imposed amplitude taken from the measured 8-22 % range,
  not as a model shortfall; the measured `drift` is an estimator of that knob.
