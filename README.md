# aaps-insilico

[![demo](https://img.shields.io/badge/demo-live-brightgreen)](https://jetfoxy.github.io/aaps-insilico/)
[![license: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

In-silico test bench for AAPS + autoISF: real algorithm code in a closed loop with a
UVA/Padova-like virtual patient (Visentin et al. 2018 equations), CGM lag/noise, and
physical exercise.

**Not** a clinical tool and not an FDA-adopted population. Output is a comparison of
algorithm variants under identical scenarios.

- Work order and acceptance criteria: [`docs/PLAN.md`](docs/PLAN.md), design and milestone
  decisions: [`docs/superpowers/specs/2026-11-10-insilico-stand-design.md`](docs/superpowers/specs/2026-11-10-insilico-stand-design.md).
- Status: W1 accepted (bridge parity on recorded Nightscout data), milestones M0–M8 accepted,
  comparisons A1–A5 accepted. The JVM bridge runs the real `UnscentedKalmanFilterPlugin`,
  `AutosensDataStoreObject`, `GlucoseStatusCalculatorAutoIsf`, the COB/autosens pipeline and
  `DetermineBasalAutoISF`. Reports: [`docs/reports/W1.md`](docs/reports/W1.md) and
  [`docs/acceptance/`](docs/acceptance/).
- Algorithm comparisons: A1 (5 subjects, 24 h), A2 (paired matrix, 30 subjects × 4 scenarios,
  bootstrap CI + Wilcoxon), A3 (CF from the model vs the 1700/TDI rule), A4 (fault catalogue),
  A5 (autoISF under faults), A6 (extended cohort, CGM noise, preference overrides), A7 (120
  subjects × 3 cohort seeds), A8 (sensor models vs real CGM traces, meal announcement errors),
  A9 (cross-check against LoopInsighT1 / UVA-Padova T1DMS), A10 (direction check against the
  simglucose level-C run), A11 (robustness to the CGM noise shape), A12 (MetaboNet as a fourth
  real-CGM source), A13 (first off / autoISF / DynISF comparison), A14 (full 30-subject
  off/autoISF/DynISF matrix), A15 (DynISF on the 120-subject cohort), A16 (DynISF under M3
  variability and on the model CF profile).
  English summary of every milestone and comparison:
  [`docs/acceptance/SUMMARY.en.md`](docs/acceptance/SUMMARY.en.md).

## Quickstart

```bash
git clone <repo> && cd aaps-insilico
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./tools/bootstrap.sh          # AAPS worktree at the pinned commit + sim-bridge classpath
.venv/bin/python -m pytest tests/ -q
```

`tools/bootstrap.sh` honours `AAPS_REPO`, `AAPS_PIN` and `AAPS_WT`; `SIM_CLASSPATH` and `SIM_JAVA`
override the bridge classpath and JVM. Without `data/raw/settings.json` the bench falls back to
`data/fixtures/prefs.example.json` (neutral APS/autoISF settings), so tests and demo export run on a
clean checkout.

Docker:

```bash
./tools/bootstrap.sh          # once: AAPS worktree + sim-bridge classpath
./tools/docker_build.sh       # flatten the classpath into the image
docker compose up -d          # http://127.0.0.1:8766/?live=1
```

The image is self-contained at runtime (JRE 21, Python, flattened bridge classpath). It is built
from the host's pinned AAPS worktree rather than cloning it, because the pinned fork is private; the
AAPS sources never enter the image, only the compiled classpath (84 MB).

## Demo UI

Hosted demo: **https://jetfoxy.github.io/aaps-insilico/** (precomputed runs; live mode needs the
local server below).

`web/` is a dependency-free static page that replays precomputed closed-loop runs: glucose (CGM vs
plasma), IOB, temp basal, SMB markers, meals, the algorithm `reason` for every cycle, and summary
metrics. No server and no build step:

```bash
python3 python/runner/export_demo.py     # writes web/data/runs.js (11 runs, ~2 min)
open web/index.html                      # works from file:// too
```

Deep link to one run: `web/index.html?run=adolescent%23001-fcl-exercise-on`; add `&compare=1` to
overlay the other arm and show on − off deltas.

Live mode runs the loop and streams cycles over SSE (needs the built bridge):

```bash
python3 python/server/live.py --port 8766
open http://127.0.0.1:8766/?live=1
```

## Parameters

Cohort size, CGM noise and algorithm preferences are shared CLI options in
`python/runner/options.py` and are accepted by `a2_matrix.py`, `fault_matrix.py`, `export_demo.py`
and `server/live.py`:

| Option | Meaning |
|---|---|
| `--cohort-copies N` | add N absorption-perturbed copies per base subject (30 → 30·(1+N)) |
| `--cohort-seed` | deterministic perturbation seed |
| `--noise residuals\|ar1\|white\|none\|facchinetti\|dexcom` | CGM noise/sensor model |
| `--noise-file PATH` | CSV/NPY residuals to replay instead of the shipped fixture |
| `--noise-scale` | amplitude multiplier (e.g. 1.5 for a noisier sensor) |
| `--noise-sigma`, `--noise-ar1` | AR(1)/white parameters, mg/dL and lag-1 correlation |
| `--pref KEY=VALUE` | override any AAPS preference (autoISF weights, SMB limits, ...) |

`facchinetti` and `dexcom` are the published sensor-error models (Facchinetti 2014; Vettoretti 2019
Dexcom G6), implemented from the papers and cross-checked against LoopInsighT1 (MIT). Meal
announcement errors are scenario fields: `announce_amount_error_frac`, `announce_time_error_min`,
`announce_lead_min` (scenario `announced-error` in `a2_matrix.py`).

The same values can come from the environment (CLI wins, then env, then built-in defaults):

| Variable | Meaning |
|---|---|
| `SIM_COHORT_COPIES`, `SIM_COHORT_SEED` | extended cohort |
| `SIM_NOISE`, `SIM_NOISE_SCALE`, `SIM_NOISE_SIGMA`, `SIM_NOISE_AR1` | CGM noise |
| `SIM_PREFS` | `key=value;key=value` overrides |
| `SIM_PREFS_FILE` | path to a JSON object with overrides |
| `SIM_ISF_SOURCE`, `SIM_TEMP_MODE`, `SIM_VARIABILITY`, `SIM_COMPARE` | run mode |
| `SIM_HOST`, `SIM_PORT` | live server bind address |

In the live UI the same parameters are editable per run: open **параметры прогона** above the
charts (cohort size/seed, noise model and amplitude, ISF source, temp mode, variability, SMB ratio,
six autoISF weights and min/max). The static demo is precomputed, so the panel applies to live runs
only.

```bash
# 120 patients (30 base + 3 copies each), noisier CGM, autoISF acceleration weight off
.venv/bin/python python/runner/a2_matrix.py --subjects 120 --workers 6 \
    --cohort-copies 3 --noise-scale 1.5 \
    --pref bgAccel_ISF_weight=0.0 --out out/a2_noisy.json
```

The extended cohort perturbs only absorption parameters (`kabs`, `kmax`, `kmin`, `ka1`, `ka2`, `kd`,
`ksc`) and recomputes the subcutaneous insulin steady state analytically, so every copy starts at the
same fasting glucose as its base subject. `Vmx`, `kp3`, `BW`, `Gb` and `u2ss` are not perturbed: the
population vector for them is proprietary and re-equilibration is not validated.

DynamicISF is **not** wired yet. `DetermineBasalSMB` takes a `dynIsfMode` flag, but the real plugin
computes `variable_sens` from a TDD history (1-day, 7-day, last 24 h, last 4 h) through a private
`calculateRawDynIsf` and a full service graph. Reimplementing that formula would violate the "no
ported algorithms" rule, so DynISF needs a bridge adapter around the real plugin and a TDD feed from
the simulated journal — a separate milestone.

## Layout

| Path | Purpose |
|---|---|
| `python/simcore/` | ODE core (S2017 appendix), solver, derived cohort, diurnal variability |
| `python/cgm/` | lag, calibration, real-residual noise, cadence, quantization |
| `python/exercise/` | intensity -> sensitivity, absorption, recovery tail |
| `python/pump/` | dose quantization, limits, delivery delay |
| `python/runner/` | closed loop, metrics, matrices, demo export |
| `python/golden/` | W1 replay and parity checks on recorded Nightscout data |
| `bridge/` | JVM module with the real AAPS/autoISF code (copied into the AAPS worktree) |
| `vendor/deichmann/` | second model for M8, BSD-3, original code |
| `web/` | static demo UI |
| `docs/` | design, parameter registry, per-milestone acceptance reports |

## W1 bridge

`bridge/` is a JVM Gradle module that is copied into a disposable worktree of the pinned AAPS
commit. It runs the real `UnscentedKalmanFilterPlugin`, `AutosensDataStoreObject`,
`GlucoseStatusCalculatorAutoIsf`, `OpenAPSAutoISFPlugin.autoISF()` and
`DetermineBasalAutoISF.determine_basal`.

```
tools/sync_bridge.sh                                  # copy bridge/ into $AAPS_WT/sim-bridge
cd "$AAPS_WT" && ./gradlew :sim-bridge:simClasspath   # write build/sim-classpath.txt
python3 python/golden/check_glucose.py --help
python3 python/golden/check_iob.py --help
python3 python/golden/check_autoisf.py --help
```

Nothing is committed to AAPS: the worktree is disposable and `sim-bridge/` exists only in it.

Acceptance reports in `docs/acceptance/` are in Russian (the working language of the project);
the code, README and commit messages are in English.

## License

GPL-3.0-or-later. The JVM bridge in `bridge/` compiles against AndroidAPS classes, and AndroidAPS
is GPL-3.0, so the combined work is distributed under the same license. The Python part does not
link AAPS and could be relicensed separately if that ever matters.

`vendor/deichmann/` is the original Deichmann et al. 2023 code under BSD-3-Clause (see its
`LICENSE` and `PROVENANCE.md`); BSD-3 is compatible with GPL-3.0. Patient data from that repository
is not vendored. AAPS itself is not vendored — the bridge is copied into a disposable worktree at
build time.
