# Acceptance summary (English)

English | [Русский](SUMMARY.ru.md)

The full reports are in Russian next to this file (`A1.md` … `A28.md`, `M0.md` … `M8.md`,
`W1.md` … `W4.md`). This page is the English entry point: what was built, what was measured, and
how to read the numbers.

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

### A8–A23 — later comparisons (compact)

| ID | Content | Result |
|---|---|---|
| A8 | Sensor models (Facchinetti 2014, Dexcom G6) vs real traces; meal-announcement errors | model σ 2.46/1.36 vs 2.8–4.4 in traces; AR(1) is a fallback only |
| A9 | Cross-check against LT1 (UVA/Padova T1DMS): 50 g, no bolus, 8 h | order of magnitude agrees; our adult peaks earlier (120 vs 203 min) and clears the meal faster |
| A10 | Direction check against simglucose level C (10 adults, 5 repeats) | direction agrees: TIR −0.4…−0.5 pp, TBR +0.4…+0.6 pp, ΔLBGI +0.16 vs our +0.15 |
| A11 | Robustness to CGM noise shape (xDrip fixture vs `resid_ma` vs `resid_quad`) | direction unchanged; TIR/TBR stable, LBGI sensitive to the spectrum |
| A12 | MetaboNet loader, Medtronic Enlite residuals | σ 1.40–1.74, PSD slope 2.6; models match by σ but are "bluer" |
| A13 | First off / autoISF / DynISF comparison, 6 subjects × 24 h FCL | DynISF sits between off and autoISF by TIR; SMB −26 %, but TBR 5.3 % and LBGI 1.38 |
| A14 | off / autoISF / DynISF, 30 subjects × 24 h FCL | DynISF vs off: TIR −2.3 pp, TBR +3.7 pp, LBGI +1.19; insulin-while-falling −8.8 pp vs autoISF |
| A15 | DynISF on the 120-subject cohort (3 arms, 360 runs) | A14 holds: ΔTIR −2.4, ΔTBR +3.8, ΔLBGI +1.17, falling −8.7 pp; p<0.0001 |
| A16 | DynISF with M3 variability and on the model CF profile | with variability TIR +1.0 pp; on the aggressive CF TIR +6.6, TBR −11.4, LBGI −2.95 |
| A17 | DynISF under faults (bolus delay, CGM gap/bias, suspend), 10 subjects | braking stable, but TBR +2…+4 and LBGI +0.5…+0.84 — worse than autoISF |
| A18 | DynISF on 120 subjects with M3 variability | vs off: TIR +0.9, TBR +1.4, LBGI +0.45; autoISF better on TBR/LBGI, DynISF still brakes (−8.0 pp) |
| A19 | DynISF on the model CF profile, 120 subjects | DynISF dominates vs off: TIR +6.1, TBR −10.9, LBGI −2.95; vs autoISF +7.7/−12.6/−3.27 |
| A20 | DynISF under faults, 30 subjects | A17 confirmed: TIR −1.0…−3.3, TBR +2.5…+4.5, LBGI +0.94…+1.19; TBR significant everywhere |
| A21 | DynISF under faults with the behaviour layer | rescue carbohydrate lowers absolute TBR/LBGI; the DynISF braking and TBR/LBGI penalty remain |
| A22 | Behaviour layer in the announced scenario | snack and announcement error do not change A2: TIR +3.0 pp, SMB −3.4 |
| A23 | Infusion-site failure | TAR +5.8/+5.4 pp, TIR −3.3/−1.9; TBR/LBGI fall; the A20 TBR/LBGI direction holds |

### A24 — compression lows

False CGM dips change the CGM metrics more than the true ones: under heavy compression CGM TBR is
14.2 % against a true 8.7 %. For DynISF the CGM and true-plasma LBGI disagree in **sign** even at
baseline, so a CGM-only low metric cannot be the sole outcome. Report: `A24.md`.

### A25 — pre-registered safety gate (30 subjects, three arms)

Criteria were hashed before the run (`3d711469b31058c0`). Medians pass, individual tails do not:
worst-subject time below 54 mg/dL is 605 / 580 / 875 min per day for off / autoISF / DynISF, with
3 / 3 / 6 severe events (longest below 54 ≥ 120 min). Exposure is 30 subject-days, so even a pass
would only bound rare events at ~36 per subject-year (rule of three). The gate is a screen, not a
safety proof. Report: `A25.md`.

### A26 — fidelity scorecard (TimSim's ten glucose signatures)

The 24 h model is inside the real envelope on 2 of 9 defined signatures (`acf30`, `noise`). The gaps
map to layers not yet enabled in that run (rescue carbohydrate, compression) and to the single-day
window (`diurnal`, `outcome`). Real single traces score 1–5 of 10; the envelope is not a pass/fail
test. Report: `A26.md`.

### A27 — fidelity scorecard with the behaviour layer

Adding rescue carbohydrate moves `hypo_rec` from 113.8 to 77.5 min (real 50–59) and leaves
`hypo_reb` at 100 % (real 23–28); the distribution signatures do not move, and the scorecard stays
2/9. The layer is necessary but not sufficient. Report: `A27.md`.

### A28 — 22-day run with ISF drift

30 subjects, 22 days (three complete weeks; `insulin_cv` needs 22 days because the rolling 24 h
window consumes one), two arms: no drift and a 15 % weekly ISF drift — the midpoint of the measured
8–22 % real weekly drift (TimSim). The older 5.8 % figure was TimSim's controller-derived drift
reading with the mechanism **off**, not a person measurement, and is not used.

| Signature | No drift | Drift 15 % | Real range |
|---|---|---|---|
| `cv` / `tail` | 39.65 / 8.11 | 40.25 / 7.87 | outside |
| `acf30` / `acf60` | 0.857 / 0.519 | 0.860 / 0.520 | inside |
| `outcome` | 44.61 | 44.97 | defined, outside |
| `hypo_rec` / `hypo_reb` | 102.5 / 68.0 | 100.0 / 67.3 | outside |
| `noise` | 6.45 | 6.42 | inside |
| `drift` | 0.00 | 6.63 [4.78–10.48] | below |
| `insulin_cv` | 0.20 | 1.42 [1.16–3.36] | below |

Scorecard: 3 of 12 in both arms. The drift is visible in insulin (7×) and not in glucose: the loop
absorbs it with dose, as theory predicts. `drift` and `insulin_cv` estimate the imposed knob over
three weekly points, so their comparison with the real envelope is a lower bound, not a model
verdict; TimSim also notes that `insulin_cv` is driver-specific. Report: `A28.md`.

### A29 — scorecard with behaviour and compression

Same 30 subjects × 22 days and 15 % drift, plus rescue carbohydrate and false CGM compressions at
3.5 events per 30 days (the midpoint of the measured 1.9–5.3). `hypo_rec` 100.0 → **55.0 min**
(real 50–59) and `compress` 0 → **4.09** (real 1.9–5.3): two gaps close with layers. `hypo_reb`
64.1 stays outside (23.2–28.4) and `tail` gets worse, 7.87 → 8.32. Scorecard: **5 of 12**.
Report: `A29.md`.

### A30 — drift amplitude sweep

Same 30 subjects × 22 days, no behaviour or compression, imposed weekly ISF drift of 8 / 15 / 22 %
(the 15 % arm is A28's). Measured `drift` is 3.39 / 6.63 / 10.05 and `insulin_cv` 1.31 / 1.42 / 2.29;
the ratio of measured to imposed drift is 0.42–0.46. The estimator consistently recovers about 44 %
of the amplitude over three weekly points, so A28's below-envelope reading is an estimator limit,
not a model result. At a 22 % amplitude the median `drift` (10.05) is inside the real 8.2–21.7 band,
so the envelope is reachable; `insulin_cv` stays driver-specific and below range. Glucose does not
move (`cv` 40.0–40.6, `hypo_rec` 100–105). Report: `A30.md`.

### A31 — template meals and M3 variability

A29 plus TimSim's seven-occasion eating template (292,140 carbohydrate entries from 147 OpenAPS
Data Commons users; 5.2 occasions/day) and M3 diurnal sensitivity. `cv` 33.67, `tail` 4.64 and
`outcome` 28.49 move into range; `acf30` 0.914 and `acf60` 0.716 move above it, and `hypo_rec`
falls to 45.0, below range. `hypo_reb` improves to 48.7 but stays out. Scorecard: still 5 of 12,
with a different composition — the fixed three-meal schedule was itself part of the CV/tail gap.
Report: `A31.md`.

### A32 — counterregulation: source review (blocked)

TimSim's `glucagon.py` exists but is refuted in its own source: its action contributes ~0.001
mg/kg/min against a 0.42–0.84 production target, its dynamic arm has the ordering backwards, and
the secretion parameters are unpublished (Dalla Man 2014; Pompa 2021). The catecholamine arm has
no published concentration-to-EGP parameters. Wiring any of this to close `hypo_reb` would be
fitting, not modelling, so the mechanism stays out and `hypo_reb` stays a reported gap with a
known direction. Reports: `A32.md`.

## What-if (W1–W4)

- **W1 import:** Nightscout read-only client, encrypted AAPS settings export, 3-day window with an
  8 h lookback, normalized trace (profile switches, TT, autosens, SMB, short temp basal). Accepted.
- **W2 replay parity:** the real CGM stays pinned; decisions are compared to `openaps.suggested`
  with fixed tolerances and per-field denominators. In-window coverage is 100%; `final_isf` 31.6%,
  `insulin_req` 43.1%, `rate` 37.2%, `smb_on` 62.8%, with an `acceIsf` residual. **Not accepted** —
  the residual is pin-limited: the bridge runs upstream AAPS `74271a544b`, while the trace was
  recorded by a newer production AAPS with the raw-observation estimator and autoISF IOB/COB inputs.
  Absolute AAPS parity is not claimed; every variant shares the same pipeline, so relative deltas
  are the intended output.
- **W3 model:** a cohort subject is matched on log TDI/CR/ISF, warmed up with real pre-window events,
  and metrics are computed on the unshifted model glucose. Accepted as an engine and an illustration
  only (distance ~0.5, low confidence; model TIR 34.4% vs real 94.5%).
- **W4 UI:** import, modes, variants, charts, metrics, delete, and privacy checks on a synthetic
  stub. Accepted.

Reports: `docs/acceptance/W1.md`–`W4.md`.

## Reproducibility

- Seeds: cohort perturbation (`--cohort-seed`), CGM noise (`crc32(subject)`), bootstrap (fixed).
- Every comparison uses paired runs with identical CGM seeds.
- 199 tests; `pytest tests/ -q` (5 bridge tests are deselected by default; run them with `-m bridge`).
