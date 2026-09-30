# Production patterns (stub)

**Status:** Intent only (2026-09-30). Not scheduled.  
**Program:** A4 Production.  
**Parent hub:** [`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md).  
**Depends on:** A3 hourly drains as data ([`HOURLY_DRAIN_POLICY_PLAN.md`](./HOURLY_DRAIN_POLICY_PLAN.md)),
asset galleries / starter roles ([`ASSET_GALLERY_MODEL.md`](./ASSET_GALLERY_MODEL.md)).

---

## Intent

**Production** is multi-stage **workproduct movement** — patterned sequences such as:

Starter → Extend → Climax → Denouement

(or other named stages). Stages consume **role slots** (starter, identity, pose,
look, …) filled from asset galleries / specialty sources — not only stills.

Hourlies remain the **scheduled manifestation** engine; Production would
eventually drive *which patterns* drain and *how stages chain*. Adhoc remains
how you discover and refine those patterns before promoting them.

---

## Non-goals (now)

- No Production runner, queue type, or UI.
- No second scheduler alongside hourly.
- Do not invent role ontologies ahead of a real pattern need.

---

## When to expand this doc

After hourly **drain policy is configuration-first** and at least one multi-stage
pattern is worth promoting from adhoc learning. Until then: capture vocabulary
here only; execute under A2/A3.
