# DynISF adapter — implementation plan

DynISF is the only target algorithm that is not runnable in the bench. This note is the concrete
plan; it is not implemented yet.

## Key fact: without TDD history DynISF starts from the profile ISF

`OpenAPSSMBPlugin.calculateRawDynIsf` only uses TDD to derive `baseSensitivity` when
`dynisf_use_profile_sens = false`. With `dynisf_use_profile_sens = true` (the setting used by this
project's reference configuration) the base sensitivity **is the profile ISF**, and the dynamic part
is the glucose scaling:

```
divisor  = peak > 65 ? 55 : peak > 50 ? 65 : 75      // peak from iCfg (45 min -> 75)
glucose  = bg > cap ? cap + (bg - cap) / 3 : bg       // cap = dynisf_bg_cap
scaler   = ln(100 / divisor + 1) / ln(glucose / divisor + 1)
ratio    = 1 - (1 - scaler) * velocity               // velocity = dynisf_velocity / 100
sens     = profileISF * ratio
```

At `bg = 100` the ratio is 1, so `sens = profileISF`; above target the ISF falls (more aggressive),
below target it rises. That is the "starting formula" the bench can run with **no TDD statistics at
all**.

The only catch is the gate `calculateVariableIsf` applies before calling the formula:

```kotlin
if (!dynIsfResult.tddPartsCalculated()) return Pair("TDD miss", null)
```

`tddPartsCalculated()` requires all five TDD windows to be non-null, but in profile-sens mode their
values are never used. So a **stub** returning a constant (the profile TDI, or zeros) is enough.

## What the code needs (checked)

- `TddCalculator` is a 7-method interface (`calculate`, `calculateToday`, `calculateDaily`,
  `calculateInterval`, `calculateIntervalWithCachedDays`, `averageTDD`). A stub can return
  `TDD(timestamp = now, totalAmount = profileTDI)` and
  `AverageTDD(data = TDD(...), allDaysHaveCarbs = true)`.
- `ProfileSealed.EPS` is already constructible: `Profiles.kt::ProfileRow.effectiveProfile(iCfg)`
  builds one from basal/isf/ic/target blocks; the sim profile comes from the request, so a
  single-block `EPS` helper is needed.
- `OpenAPSSMBPlugin` constructor: 25 dependencies; `DetermineBasalSMB(profileUtil, fabricPrivacy)`
  and `GlucoseStatusCalculatorSMB(logger, iobCobCalculator, dateUtil, ConsoleDecimalFormatter(),
  DeltaCalculator(logger))` are the two real ones, the rest are proxies (same pattern as
  `AutoIsfRunner`).
- `getIsfMgdl(eps, "bench")` is the entry point that runs the real DynISF formula.
- The decision function is `DetermineBasalSMB.determine_basal(..., dynIsfMode = true, smb_ratio)`,
  which takes `OapsProfile` (not `OapsProfileAutoIsf`), so a second profile mapper is needed.

## Design

1. **Bridge command `dynisf`** (new), mirroring `autoisf`:
   - build a `ProfileSealed.EPS` around the simulated profile (the `getIsfMgdl` path casts to it);
   - instantiate `OpenAPSSMBPlugin` with the same proxy graph as `AutoIsfRunner`, plus:
     - `TddCalculator` stub returning `TDD(totalAmount = profileTDI)` for all five windows,
     - a real `DetermineBasalSMB` (only `ProfileUtil` + `FabricPrivacy`),
     - a real `GlucoseStatusCalculatorSMB` over the existing `iobCobCalculator`,
     - `glucoseStatusProvider` returning the current status,
     - `profileFunction.getProfile()` returning the same profile;
   - `sens = plugin.getIsfMgdl(eps, "bench")`.
2. **Bridge command `stepSmb`** (new): `DetermineBasalSMB.determine_basal(..., dynIsfMode = true,
   smb_ratio = ...)` with `profile.variable_sens = sens`. This is a different decision function from
   `DetermineBasalAutoISF`, so the loop needs an `algorithm` switch.
3. **Loop.** `AlgorithmProfile.algorithm = "autoisf" | "dynisf"`; the DynISF arm calls `dynisf` +
   `stepSmb`, the autoISF arm keeps `autoisf` + `step`.
4. **Settings.** Use the reference configuration: `dynisf_use_profile_sens = true`,
   `dynisf_velocity = 75`, `dynisf_bg_cap = 252`, `use_dynamic_sensitivity = true`.

## Risks

- `getIsfMgdl` casts the profile to `ProfileSealed.EPS`; the bridge must wrap the simulated profile.
- `OpenAPSSMBPlugin` has ~25 constructor dependencies; the proxy graph must answer the ones used on
  the ISF path (`profileFunction`, `glucoseStatusProvider`, `profileUtil`, `dateUtil`,
  `preferences`, `persistenceLayer`). This is the bulk of the work.
- `DetermineBasalSMB` is large and expects an `OapsProfile` with `TDD`, `insulinDivisor` and
  `use_TDD_for_predictions`; these must be set from preferences, not hand-written.
- **No golden data.** Acceptance is structural, not parity.

## Status (2026-11-11)

The ISF half is implemented and validated:

- `DynIsfRunner.kt` instantiates the real `OpenAPSSMBPlugin` with a proxy graph, a constant
  `TddCalculator` (profile TDI) and the real `DetermineBasalSMB` / `GlucoseStatusCalculatorSMB`.
- Bridge command `dynisf` builds a single-block `ProfileSealed.EPS` from the request and returns
  `getIsfMgdl(eps, "bench")`.
- Measured with `isf = 50`, target 100, velocity 75, cap 252: BG 100 → 50.0, BG 150 → 41.42,
  BG 200 → 36.95, BG 250 → 34.17. At target the ISF equals the profile ISF (ratio 1), and it falls
  monotonically with BG, as the formula requires.
- One real bug found and fixed on the way: `profileUtilProxy` did not implement
  `convertToMgdlDetect`, so `dynisf_bg_cap` became 0 and the cap folded BG 100 into 33.3.

`stepSmb` and the loop arm are implemented too:

- `SimBridge.toOapsProfileSmb()` maps the request profile to `OapsProfile` (including `variable_sens`,
  `insulinDivisor`, `TDD`, `use_TDD_for_predictions`).
- `stepSmb` calls `DetermineBasalSMB.determine_basal(..., dynIsfMode = true)` and returns the RT.
- `AlgorithmProfile.algorithm = "dynisf"` + `tdd`; the loop calls `dynisf` for `variable_sens` and
  `stepSmb` for the decision. A 2 h FCL run works: 24 cycles, ISF 23.4–30 (profile 30), dose 6.75 U,
  `runningDynamicIsf = true` in the RT.
- Tests: `tests/test_dynisf.py` (ISF at target, monotone fall, loop arm).

Remaining: a DynISF vs autoISF vs off matrix on the 30-subject cohort, and wiring the `dynisf` arm
into `a2_matrix.py` / the live UI.

## Acceptance (proposed)

1. `dynisf` returns a finite `sens` for every subject, deterministic by seed.
2. At `bg = target` the returned `sens` equals the profile ISF (ratio = 1).
3. Higher BG lowers `sens` monotonically; the response saturates at `dynisf_bg_cap`.
4. A 24 h FCL run with the DynISF arm has no NaN, no negative rates and a dose in the TDI range;
   compared side by side with autoISF off/on as a third arm.
