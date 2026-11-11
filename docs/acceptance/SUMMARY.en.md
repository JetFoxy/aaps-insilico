# Acceptance summary (English)

The full reports are in Russian next to this file (`A1.md` … `A6.md`, `M0.md` … `M8.md`). This page is
the English entry point: what was built, what was measured, and how to read the numbers.

**Nothing here is clinical evidence.** The cohort is a *derived* S2008/S2013 set on S2017 equations,
not the proprietary S2017 population; absolute TIR/TBR values should not be compared with trials.

## Milestones

| Stage | Content | Acceptance |
|---|---|---|
| W1 | JVM bridge running the real `UnscentedKalmanFilterPlugin`, autosens, COB and `DetermineBasalAutoISF` on recorded Nightscout data | 250 cycles, glucose 94.8 % exact, IOB p90 0.013 U, rate ±0.05 in 82.4 % (`docs/reports/W1.md`) |
| M0 | Parameter registry with a source and page for every number | 68 rows: 13 published, 55 TODO (`docs/registry/`) |
| M1 | S2017 core, 13 states | steady state drift 6.5e-10, meal balance 0.000 % |
| M2 | Derived cohort S2008/S2013 | 30 subjects, weight/TDI/CR within ±1 SD of S2017 |
| M3 | Diurnal SI classes and dawn | 7 classes, dawn +1.5 EGP; dawn rise 38–48 vs ~30 published (documented) |
| M4 | CGM chain | real residual noise: sigma 2.49, PSD slope 3.72 vs 3.59 synthetic |
| M5 | Exercise | alpha 3.29, recovery 165 min, ka ×1.388 |
| M6 | Pump and closed loop | open loop = M1 (atol 1e-9), deterministic, 2–3 ms per bridge call |
| M7 | Scenario matrix, metrics, faults | 39 runs, 30 subjects, stable seeds |
| M8 | Second model (Deichmann 2023, BSD-3) | meal and moderate exercise agree; high intensity differs (no rGP) and is explained |

## Comparisons

### A1 — first autoISF on/off run (5 adolescents, 24 h FCL)

| Metric | off | on |
|---|---|---|
| TIR 70–180 | 0.751 | 0.731 |
| TBR <70 | 3.7 % | 5.7 % |
| LBGI | 1.02 | 1.49 |
| Dose, U | 34.0 | 35.0 |
| SMB | 91.8 | 78.6 |

Small sample, one subject drives the aggregate — the reason A2 exists.

### A2 — paired matrix (30 subjects × 4 scenarios, bootstrap CI + Wilcoxon)

| Scenario | TIR off→on | TBR<70 off→on | Dose off→on | SMB off→on | Insulin while falling |
|---|---|---|---|---|---|
| FCL 24 h | 0.714→0.706 | 7.0→8.0 % | 33.0→33.7 U | 90→77 | 0.299→0.364 |
| FCL + exercise | 0.730→0.727 | 11.8→12.2 % | 15.1→15.6 U | 41→37 | 0.355→0.409 |
| Announced 8 h | 0.703→**0.716** | 3.0→2.8 % | 17.6→18.0 U | 44→42 | 0.297→0.344 |
| Profile error ×0.7 | 0.652→0.641 | 16.4→17.5 % | 35.6→36.5 U | 86→75 | 0.262→0.337 |

autoISF cuts SMB by 8–17 %, adds 1.6–6.3 % insulin, raises insulin-while-falling by 4–8 pp, keeps
TIR within ±1.6 pp and slightly increases hypoglycaemia (TBR +0.4…+2.5 pp, LBGI up significantly in
4 of 5 scenarios). With M3 variability the same picture holds; under a ×0.5 profile error both arms
spend ~26 % below 70 and autoISF neither helps nor hurts.

### A3 — CF from the model vs the 1700/TDI rule

Measured on the model, the correction factor is **2.4× smaller** than the clinical rule (median 25.7
vs 50.6 mg/dL per U; ratio 0.30–0.91). Using it makes the loop *worse*: TIR 0.626 vs 0.714 and TBR
21.2 % vs 7.0 %. The model's insulin action has a long tail (nadir at ~365 min for a 1 U bolus) while
meals absorb in 3–4 h, so "physically consistent" corrections land after the meal and keep acting.
The rule stays the baseline; the model CF is a consistency probe.

### A4 — fault catalogue (10 adolescents, 24 h FCL)

| Fault | TIR | ΔTIR | TBR<70 | LBGI |
|---|---|---|---|---|
| none | 0.734 | — | 3.1 % | 0.87 |
| bolus delay 15 min | 0.704 | −3.0 pp | 5.0 % | 1.19 |
| CGM gap 60 min | 0.717 | −1.7 pp | 3.4 % | 0.94 |
| CGM bias +50 mg/dL, 2 h | 0.698 | −3.7 pp | 5.6 % | 1.18 |
| pump suspend 60 min | 0.722 | −1.3 pp | 2.1 % | 0.67 |

### A5 — autoISF under faults

autoISF loses TIR in every fault and never wins: −1.8 pp clean, **−2.9 pp** under bolus delay
(p = 0.008), −1.6 pp with a CGM gap, −0.7 pp under a CGM bias, −2.4 pp after a suspend. TBR rises
0.9–2.8 pp, LBGI rises in 10/10 subjects in all five cases. Mechanism: autoISF lowers ISF and does
not brake on falling glucose, which is tolerable on clean data and costly when inputs are late or
wrong.

### A6 — extended cohort, noise and parameter knobs

- **Cohort:** 30 → 30·(1+N) by perturbing absorption parameters only (`kabs`, `kmax`, `kmin`, `ka1`,
  `ka2`, `kd`, `ksc`) and recomputing the subcutaneous insulin steady state analytically. Copies keep
  fasting glucose (drift 2·10⁻¹⁰ mg/dL). `Vmx/kp3/BW` are not perturbed (proprietary vector, no
  validated re-equilibration).
- **Noise:** real residuals (default), AR(1), white, or none, with an amplitude multiplier.
- **Algorithm parameters:** any AAPS preference via `--pref` / `SIM_PREFS`, including the six autoISF
  weights and min/max; also exposed per run in the live UI.
- **Docker:** runtime image (851 MB) with the flattened bridge classpath; built from the host's
  pinned AAPS worktree.

### A7 — extended cohort, three seeds (120 subjects, 1440 runs)

With 30 base subjects plus 3 absorption-perturbed copies each, and three independent cohort seeds:

| Metric (FCL 24 h) | seed 20261111 | seed 7 | seed 42 | mean |
|---|---|---|---|---|
| ΔTIR | −0.009 (p=0.044) | −0.008 (p=0.051) | −0.009 (p=0.015) | −0.0087 |
| ΔTBR<70 | +0.010 | +0.011 | +0.011 | +0.0107 |
| ΔSMB | −13.2 | −13.6 | −13.4 | −13.4 |
| Δdose, U | +0.62 | +0.62 | +0.65 | +0.63 |
| Δinsulin while falling | +0.065 | +0.066 | +0.065 | +0.065 |

Exercise: ΔTIR −0.002 pp (p = 0.014–0.19), ΔTBR +0.0034, ΔSMB −4.3, Δdose +0.55 U. The effect is
stable across seeds (spread ≤ 0.1 pp) and matches A2's 30-subject result to within 0.1 pp / 0.1 U;
at n = 120 the FCL TIR loss becomes statistically significant.

## Reproducibility

- Seeds: cohort perturbation (`--cohort-seed`), CGM noise (`crc32(subject)`), bootstrap (fixed).
- Every comparison uses paired runs with identical CGM seeds.
- 57 tests; `pytest tests/ -q`.
