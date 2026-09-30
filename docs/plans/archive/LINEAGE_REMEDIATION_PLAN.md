> **Archived draft** — see [MAP.md](MAP.md) and live docs under [`docs/plans/`](../). Do not treat this file as current authority.

# Lineage & asset remediation

**Program:** S1 Custody (child of asset lifecycle). Hub:
[`PLANNING_OVERVIEW.md`](./PLANNING_OVERVIEW.md).

**Status:** Design for future implementation (2026-09-29).  
**Parents:** [`ASSET_LIFECYCLE_PLAN.md`](./ASSET_LIFECYCLE_PLAN.md) (Phases 2–3 locate/relocate),
[`APPETITE_REMOVE_LIFECYCLE.md`](./APPETITE_REMOVE_LIFECYCLE.md),
[`CORPUS_LIFECYCLE.md`](./CORPUS_LIFECYCLE.md) (judgment vs custody),
[`LINEAGE_INDEX_SKETCH.md`](./LINEAGE_INDEX_SKETCH.md).  
**Steer / Out / descendant judgment:** [`CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md`](./CHAIN_STEER_AND_APPETITE_CONTROL_PLAN.md).

---

## Intent

A **general remediation process** for broken or intentionally changed lineage and
path references. Classify first; then apply one action from a small menu. Keep the
graph **honest** when bytes are gone, moved, or rotten — never pretend a dead
path is live.

Two lifecycles stay distinct:

| | Question |
|--|----------|
| **Asset / custody** | Where is the file, and do pointers work? |
| **Corpus / judgment** | What do we want next? (in-question children, Keep vs Out) |

This doc is mostly **custody**, plus the queues that join judgment when a parent
is removed.

---

## Break kinds

| Kind | What you see | Typical cause |
|------|----------------|---------------|
| **Missing bytes** | Ref → no file at current or alias paths | trash/Out, purge, unbound move, disk loss |
| **Stale path** | File exists elsewhere | relocate without rewrite |
| **Corrupt** | Present but unreadable / truncated / decode fail | bit-rot, partial write |
| **Hash mismatch** | Path exists but bytes ≠ expected `content_id` | wrong file, silent overwrite |
| **False edge** | Link that should not exist | bad inference, stem collision |
| **Policy orphan** | Parent intentionally gone; child kept | Out / remove / trash on parent |
| **Dangling forward** | Parent terminal; children not reviewed | cascade never queued |
| **Identity split** | Same logical asset, two ids/paths | rename/re-encode without `lineage_uid` |

---

## Remediation menu

| # | Action | When |
|---|--------|------|
| 1 | **Locate + rebind** | Bytes still exist; verify; rewrite ref |
| 2 | **Relocate + rewrite** | Planned move (reorg, space, Out→trash); update map + all refs |
| 3 | **Restore** | Recover from `og/_trash/` (or backup) when mark/move was wrong |
| 4 | **Tombstone parent** | Parent stays gone; edge marked `parent_missing`; child first-class |
| 5 | **Accept orphan / re-root** | Child kept; soften or clear parent link; provenance note |
| 6 | **Drop edge** | False positive; delete/quarantine edge only |
| 7 | **Queue for review** | Judgment required (descendants in question; Keep vs gone parent) |

**Not remediations:** silent delete of descendants; inventing placeholder files so
paths “work.”

### Process shape

```text
audit broken refs / edges
  → classify
  → if auto-safe: locate/rebind or relocate/rewrite (verify first)
  → else: remediation queue + suggested action + evidence
  → operator: Restore | Rebind | Tombstone | Accept orphan | Drop edge | Trash child
```

**Auto-safe** = content-verified match (`content_id` / `lineage_uid`) or a single
trash hit for that stem after verify. Policy orphans and fan-out to children
always go through review (7).

---

## Special case: intentional relocate (old → new map)

Space and directory-structure moves are **planned** breaks. Every relocate must:

1. Move media + companions (atomic or resume-safe).
2. Append **old → new** under stable id (`content_id` / `lineage_uid`).
3. Set `current_relpath`; keep full `moved_history`.
4. Rewrite live refs (jobs, pools, lineage endpoints, disposition keys, bins if
   path-keyed, discovery tips).
5. Leave **alias resolution**: any historical path → id → current path so
   stragglers and audit JSONL still resolve until rewritten.

Registry already has `current_relpath`, `moved_history`, `status`
(`present` / `missing`) — see [`ASSET_LIFECYCLE_PLAN.md`](./ASSET_LIFECYCLE_PLAN.md)
Phases 2–3. Out → `og/_trash/<date>/` is the same machine with
`reason=steer_out` or `retire_trash`.

**Invariant:** set of content ids unchanged across a reorg pass ⇒ nothing lost,
only pointers moved.

---

## Special case: parent terminal ↔ descendants

| Case | Process |
|------|---------|
| **A. Parent deleted / Out / trashed** | Do not auto-delete children. Gate purge while referenced (`has_references`). Flag **direct** descendants **in question** for case-by-case Keep / Out / Remove. |
| **B. Child kept, parent gone** | Orphan remediation: Restore parent, **Tombstone**, or **Accept orphan**; fix job bindings; UI shows tombstone not 404-hang. |

Fan-out is one hop per review pass unless the operator explicitly widens.

---

## Data rot, missing, corrupt (graceful)

| Status | Behavior |
|--------|----------|
| `present` | Verified readable (and hash match when expected) |
| `trashed` | Under `og/_trash/`; recoverable |
| `missing` | No verified bytes; refs kept; lists/ticks skip bind |
| `corrupt` | Bytes untrusted; quarantine; never bind into new jobs |

**Graceful everywhere:**

- Previews / lineage / backlog: tombstone or missing/corrupt chip — no hang/500.
- Factory submit: refuse Load* on failed verify.
- Edges: keep history with endpoint status; don’t delete the graph because files died.
- **Hash mismatch:** refuse silent rebind; locate real id or tombstone the ref.

### Locate ladder (stills already partially real)

1. Registry `current_relpath`
2. `moved_history` aliases
3. Basename / hash-in-name under known roots (incl. unsorted)
4. `og/_trash/`
5. Remote still recovery (`aigc.uploads.dev` + **sha verify**) — `.cursor/rules/asset-recovery.mdc`
6. Fail → tombstone (+ optional drop false edge / accept orphan)

Corrupt or mismatch **never** advances to “rebind to whatever sits at that path.”

---

## Relation to remove review (today)

| Today | Gap |
|-------|-----|
| `purge_ready` / `has_references` blocks unlink | No post-gate **descendants_in_question** queue |
| Retire **Trash** moves to `og/_trash/` | Steer **Out** not yet wired to same move |
| Discovery resolves trash for Retire rows | No general alias map for arbitrary reorg |
| Asset registry columns exist | Phase 2 audit / Phase 3 relocate CLI not productized |

---

## Implementation slices

### Available now

| Slice | Scope | Notes |
|-------|--------|-------|
| **R1 — Wire Out → `trash_output_media`** | Workproduct video Out calls existing trash + disposition rekey | Same as steer plan **S3**; highest shared payoff |
| **R2 — Alias resolve helper** | `resolve_output_path(rel) → abs` via live path, then `_trash` by stem, then registry `moved_history` if populated | Unblocks previews when files already trashed |
| **R3 — Audit report (read-only)** | Scan chain/job/pool refs for missing paths; emit classified report (no rewrite) | Phase 2 `assets audit` spike; no UI required |

### Next

| Slice | Scope | Blocker |
|-------|--------|---------|
| **R4 — `assets relocate`** | Move + companions + history + rewrite one id | Careful ref inventory |
| **R5 — Descendants-in-question** | Queue + UI after parent terminal | Disposition step or light queue store |
| **R6 — Orphan remediation actions** | Tombstone / accept orphan / restore on kept child | Edge schema for `parent_missing` |
| **R7 — Corrupt / verify gate** | `last_verified_at` + refuse bind on fail | Cheap decode/hash policy per media kind |

### Later

| Slice | Scope |
|-------|--------|
| Full Phase 2 locate apply (fuzzy + remote ladder productized) | |
| Phase 4 image reorg via relocate | |
| Identity split merge (`lineage_uid` mint) | |
| Bulk remediation UI over audit NDJSON | |

### Recommended order

1. **R1** with steer **S3** (one Out→trash path).
2. **R2** so trashed/Out items don’t break UI.
3. **R3** to measure rot before rewrite automation.
4. **R4** when directory/space pressure forces a real reorg.
5. **R5–R7** with steer descendant/habit work.

---

## Non-goals

- Auto-cascading delete of the descendant tree.
- Placeholder / stub media files for missing parents.
- Replacing content-addressed identity with path-only keys.
- Blocking hourly on a full remediation UI.
