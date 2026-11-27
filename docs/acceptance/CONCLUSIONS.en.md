# What the bench shows — consolidated conclusions

[Русский](CONCLUSIONS.ru.md) | English

This page condenses A1–A46 and M0–M8. The detailed reports are next to it; the canonical numbers
come from the strict-profile runs (A39, A40, A45). **Nothing here is clinical evidence.** The cohort
is a derived S2008/S2013 set on S2017 equations, not the proprietary S2017 population.

## 1. What the bench can answer

It compares algorithm variants under identical scenarios: same real Kotlin AAPS bridge, same
subjects, same CGM seeds, paired statistics. Relative deltas are the intended output, and they are
stable across seeds and cohort sizes (A7, A15).

## 2. Canonical results

Baseline off-arm on 27 valid profiles, 24 h FCL: **TIR 0.740, TBR<70 0.037, LBGI 1.05** (A39).

| Question | Result | Report |
|---|---|---|
| autoISF vs off | TIR −1.3 pp, TBR +1.5 pp, LBGI +0.4, SMB −14 %, dose +0.85 U; no TIR gain | A2/A39 |
| autoISF with announced meals | TIR +1.5 pp (A39); +3.2 pp with a snack (A45); TBR stays 0 | A2/A22/A45 |
| DynISF vs off | TIR −2.1 pp, TBR +3.2 pp, LBGI +0.91; brakes in the fall | A14/A39 |
| DynISF on the model CF | **TIR +8.9 pp, TBR −15.2 pp, LBGI −4.23** — dominance, but only when the baseline CF is wrong | A16/A19/A45 |
| Faults | mean penalties hold (autoISF TBR +1.1…2.7 pp, DynISF +1.9…4.6 pp; LBGI +0.32…0.98), but paired medians are ~0 except bolus delay — heavy-tailed burden | A4/A5/A20/A45 |
| Site failure / compression | no differential TBR; LBGI still rises | A23/A24/A45 |

## 3. Safety is a tail property

The pre-registered gate (A25) fails every arm on individual tails even when medians pass. On 660
subject-days with template meals (A40), the template arm fails only `longest below 54` (150 > 120
min), while fixed meals fail three criteria; severe events are 2 vs 4. Median TIR is 0.798 vs
0.723, and P90 time below 54 is 30.5 vs 147.3 min/day. The lispro PK variant even passes the gate
(A43), with zero severe events, but that is a screen over 594 subject-days and one seed — the
rule-of-three bound is ~1.7 severe events per subject-year. **No arm is shown safe.**

## 4. Fidelity: 7/12 is the current ceiling

The best scorecard (template meals, no M3, 27 subjects) puts **7 of 12** signatures inside the
TimSim real envelope: `tail`, `acf30`, `acf60`, `outcome`, `hypo_rec`, `compress`, `noise` (A40).
What moved the number was realistic eating, not a new physiological layer: the fixed 3×50 g
schedule itself inflated CV/tail (A34/A40). M3 variability made `acf60`, `diurnal` and `hypo_rec`
worse (A31/A33).

Robust signatures: `compress` and `noise` (P≈1.0), `acf60` (0.96), `tail` (0.83), `outcome` (0.75),
`acf30` (0.73). Fragile: `hypo_rec` (0.54, sits exactly on 50.0). Outside: `diurnal` 67.5,
`hypo_reb` 50, `cv` 35.2, `insulin_cv` 2.5.

## 5. What is blocked, and why

- **Counterregulation** (A32): action parameters are published (Wendt 2017: Emax 56–84
  µmol/kg/min, CE50 141–436 pg/ml; Emami/Haidar), but every model takes glucagon concentration as
  input and excludes the endogenous response to hypoglycaemia; secretion driven by falling glucose
  is unpublished, and the glucose-dependence of hepatic sensitivity is unstudied (Hinshaw 2015).
  Wiring it would be fitting; only an exogenous rescue scenario is legitimate.
- **Insulin action parameters** (A44): the model's action curve peaks at 83 min with a ~304 min
  tail — inside the published rapid-analog envelope (60–120 min, ~4–5 h). There is no source-backed
  reason to change `p2u/ki/m1–m4`; the long glucose nadir is a dose/meal/controller interaction.
- **Lyumjev PK** (A46): the label mean curve is fitted to 3.5 % over 1–5 h, but Tmax is 77 min
  against 57 and 50 % Cmax is 43 min against 13. The pre-registered criterion fails; no variant.
- **W2 absolute replay parity** (W2): the pin lacks the raw-observation estimator and autoISF
  IOB/COB inputs used by the recording; only relative deltas are claimed.
- **S2017 population vector**: proprietary, so the cohort remains derived; page-level provenance
  of several parameters is still `todo`.

## 6. What would change these conclusions

1. A published glucagon/catecholamine model with EGP flux parameters (would open `hypo_reb`).
2. A published absorption model for Lyumjev/URLi (would open the ultra-rapid variant).
3. A controller/scenario change that targets the post-meal SMB train (would open `diurnal` and
   `longest-low`), with pre-registered criteria.
4. Licensed S2017 subject parameters (would turn the derived cohort into the real population).
5. More subject-days and seeds (would make the gate informative about rare events).

Until then, the honest summary is: **the bench is a relative comparison tool, not a safety or
efficacy proof.** Its strongest results are the conditional DynISF benefit on a bad CF, the
consistent LBGI cost of autoISF/DynISF, and the heavy-tailed nature of every TBR penalty.
