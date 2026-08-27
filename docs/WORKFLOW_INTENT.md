# Workflow intent — factory + pipeline

**Metaphor:** Shape Factory is the plant. A **family** is a station / product line
(shape + pools + jobs). A **pipeline** is the plan a multi-step job follows
(ordered stations + bind rules). Already real as `*.pipeline.yaml`; hourly drains
are informal pipelines.

There is no separate “route” or “playbook” layer — pipeline *is* the plan.

## Station specs (Phase 1)

Each shape declares:

- `primary_input` / `input_profile` / `chain_role` / `io_class`

See [`.data/shapes/README.md`](../.data/shapes/README.md).

## Pipeline catalog (Phase 3 nascent)

Named multi-family plans live under [`.data/pipelines/`](../.data/pipelines/).
Soft guidance (`input_guidance`, affinities) is documented in
[`.data/pipelines/CATALOG.md`](../.data/pipelines/CATALOG.md) — descriptive first,
not a lockout engine.

## Family discovery (Phase 2)

Corpus clustering → provisional `prop_NNN` cards → human naming gate → enroll with
Phase 1 vocabulary. See [`docs/family_discovery/`](family_discovery/).
