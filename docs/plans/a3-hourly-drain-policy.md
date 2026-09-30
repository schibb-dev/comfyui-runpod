# A3 — Hourly drain policy (U1 / M1)

**Program:** A3 Refinement — **primary Next** slice.  
**Parent:** [`a3-refinement.md`](a3-refinement.md).  
**Hub:** [`README.md`](README.md).  
**Status:** Design → implement configuration-first drains.

## Intent

Express hourly drain / seed / cadence knobs as **data** (policy file), with env
as override until readers land. This is the **U1/U5 design vehicle** — not a
second ruleset beside the utility parent.

## Today

- Cadences and weights largely hardcoded / env (`HOURLY_*`).
- Guide bins and some steer soft-bias exist; chain drains still special-cased.

## Near-term

1. Land `hourly-policy.yaml` (or equivalent) + reader order: file → env → code.
2. Lift first knobs: facial / i2v→gex cadences, seed-over-chain share, lookbacks,
   seed family weights.
3. Wire steer-mark **consumption** into drain selection (marks owned by A2).

**Exit:** change GEX2 weight or facial cadence without editing Python.

## Archived sources

- [`archive/HOURLY_DRAIN_POLICY_PLAN.md`](archive/HOURLY_DRAIN_POLICY_PLAN.md)
- [`archive/HOURLY_UTILITY_PLAN.md`](archive/HOURLY_UTILITY_PLAN.md) (§U1 / milestones)

Conflicts: [`archive/MAP.md`](archive/MAP.md).
