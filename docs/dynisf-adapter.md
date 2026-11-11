# DynISF adapter — implementation plan

DynISF is the only target algorithm that is not runnable in the bench. This note is the concrete
plan; it is not implemented yet.

## What AAPS already gives us

- `DetermineBasalSMB.determine_basal(..., dynIsfMode: Boolean, smb_ratio: Double)` — the real
  decision function, with the dynamic-ISF branches inside.
- `OpenAPSSMBPlugin.getIsfMgdl(profile: Profile, caller: String): Double?` — public; internally
  calls the private `calculateVariableIsf` → `calculateRawDynIsf`, i.e. **the real DynISF formula**.
- `calculateRawDynIsf` needs:
  - `TddCalculator` (1 day, 7 days, last 24 h, last 4 h, 8–4 h),
  - `ProfileUtil`, `Preferences` (`ApsDynIsf*`), `DateUtil`,
  - a `ProfileSealed.EPS` profile (the method casts to it).

## Design

1. **Bridge command `dynisf`** (new), mirroring the existing `autoisf` command:
   - build `ProfileSealed.EPS` from the effective profile (the bench already builds `EffectiveProfile`
     for `autoisf`);
   - instantiate `OpenAPSSMBPlugin` with the same proxy graph used by `AutoIsfRunner`, plus a real
     `TddCalculator`, a real `DetermineBasalSMB` and the existing `GlucoseStatusCalculatorSMB`;
   - `sens = plugin.getIsfMgdl(eps, "bench")`;
   - `rt = determineBasalSMB.determine_basal(..., dynIsfMode = true, smb_ratio = ...)`.
2. **TDD feed.** `SimTddCalculator` implements the five `TddCalculator` methods from the loop's dose
   journal (basal + bolus per interval, carbs per interval). It returns `TDD(totalAmount, carbs,
   allDaysHaveCarbs = true)`.
3. **Loop.** `AlgorithmProfile.enable_dynisf`; the runner calls `dynisf` instead of `autoisf` and
   passes the returned `sens` as `variable_sens` with `dynIsfMode = true`.
4. **Warm-up.** `calculateVariableIsf` asks for whole days; a run that starts at 00:00 has no
   1-day/7-day history at the first cycles. Options: (a) pre-seed the TDD calculator with the
   subject's basal TDI for the first day, (b) start DynISF after 24 h of simulated history,
   (c) allow `allowMissingDays = true`. This is a decision to make explicitly and document.

## Risks

- `getIsfMgdl` casts the profile to `ProfileSealed.EPS`; the bridge must supply that wrapper, not a
  bare `EffectiveProfile`.
- `OpenAPSSMBPlugin` has ~25 constructor dependencies; the proxy graph must answer the ones used on
  the ISF path (`profileUtil`, `dateUtil`, `preferences`, `tddCalculator`, `profiler`). This is the
  bulk of the work and the main source of debugging.
- `DetermineBasalSMB` is large; the bridge needs its `OapsProfile` fields (`TDD`, `insulinDivisor`,
  `use_TDD_for_predictions`) set from preferences, not from a hand-written formula.
- **No golden data.** Acceptance must be structural: determinism, finite outputs, monotone response
  of `variable_sens` to TDD, and no change when `dynIsfMode = false`. Parity with the app is not
  available without a recorded DynISF run.

## Acceptance (proposed)

1. `dynisf` returns a finite `sens` for every subject and is deterministic by seed.
2. Raising the simulated TDD lowers `variable_sens` (higher ISF) monotonically over a sweep.
3. With `dynIsfMode = false` the command returns the profile ISF unchanged.
4. A 24 h FCL run with DynISF produces a trace with no NaN, no negative rates and a dose in the
   TDI range; compared side by side with autoISF off/on as a third arm.
