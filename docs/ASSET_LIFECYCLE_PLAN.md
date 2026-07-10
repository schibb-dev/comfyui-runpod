# Asset lifecycle: registry, job backfill, relocation, image reorg

Status: **Phases 0–1 shipped** (content registry + job backfill). Phases 2–4 planned.

- Phase 0 registry: done (`asset_registry.py`, v2 with `mtime` rehash cache).
- Phase 1 backfill: done — 133 synthetic jobs across 4 shaped families
  (X-KNEEL-FB9 40, FB9-FaceBlast 40, FB9_GEX 23, FB9_GEX2 30), stills **and**
  video-source chains reconstructed; FaceBlast's 29 lost source stills recovered
  (content-verified) and re-registered.
- Next up: Phase 2 (locate/audit) — the remote-recovery leg is already captured
  in `.cursor/rules/asset-recovery.mdc`.

## Motivation

Three interrelated needs surfaced while wiring source stills into the factory map:

1. **Backfill jobs** — "create the jobs that would have been" for outputs that were
   deposited before job tracking existed (e.g. X-KNEEL seed videos). They should
   become first-class jobs so the map, lineage, and heuristics treat them uniformly.
2. **Asset relocation** — both senses:
   - *Relocate*: intentionally move an image/video to another location.
   - *Locate*: re-find a lost/renamed/moved asset and repair references to it.
3. **Image reorganization** — `input/` is a flat ~6,790-entry dump (hash-named
   jpegs, content duplicates, Windows `:Zone.Identifier` ADS junk, scraped
   `… _files` / `New folder` dirs) that needs structure.

All three depend on one missing foundation: **stable content-based asset identity**
plus a **registry** that tracks where each asset currently lives and who references it.
Today identity is name/path-based everywhere (job abs paths, pool `index.json` paths,
discovery `og:stem:…` / `input:<basename>` group ids), so any move/rename breaks links.

## Foundation: asset registry (`content_id`)

`asset_registry.sqlite` under `output/output/_status/`:

| column | meaning |
| --- | --- |
| `content_id` | sha256 of file bytes (primary identity) |
| `size`, `ext`, `kind` | bytes, extension, `image`/`video`/`other` |
| `width`, `height` | media dims when cheaply available |
| `current_relpath` | last known location (relative to output/workspace roots) |
| `first_seen`, `last_seen`, `status` | lifecycle (`present` / `missing`) |
| `phash` | reserved for perceptual-hash near-dup detection (later) |
| `moved_history` | json list of prior relpaths |
| `refs` | json: jobs/pools/workflows/xmp that cite it |

Exact sha256 now; `phash` column reserved so near-dup dedupe can be added without a
migration. SQLite (not JSON) to scale past 6,790 rows and support queries.

**`content_id` is the canonical identity.** Fragile keys (absolute paths,
`og:stem:*`, `input:<basename>`) should resolve *through* `content_id`; every
reference (jobs, pools, lineage, ratings) can join on it, and renamed/duplicate
copies collapse to one row automatically (name-independent).

**Efficiency:** hash each file once and cache it keyed by `(relpath, size, mtime)`
so repeated/full-tree scans skip rehashing unchanged files (implemented in
`asset_registry.register`, `mtime` column, v2). Once content is the join key,
dedup stores the bytes once (hardlink / registry pointer) instead of N physical
copies — the primary space win.

**Canonical naming (target state):** migrate images to content-addressed names
`<sha256>.<ext>` so the filename *is* the identity — self-verifying (accuracy)
and dedup-by-construction (two copies land on the same name). Renaming must go
through the relocate machinery (Phase 3) to rewrite every reference
(job `LoadImage` paths, pools), and the original names are kept as aliases in the
registry (`moved_history`) for provenance and to match legacy references. Some
existing assets are *already* content-named behind prefixes (`SSS`/`XXX`/`000-`/
…); those verify for free (see `.cursor/rules/asset-recovery.mdc`).

## Media identity: images vs video

Identity is split by media type because the constraints differ:

- **Images — content hash.** `content_id = sha256(bytes)`. Cheap, permanent
  (bytes don't change post-generation), often already the filename. Keep as-is.
- **Video — identity hash in metadata, not byte hash.** Byte hashing video is
  both expensive (tens of GB) and *unstable* (any re-encode/re-mux/metadata
  rewrite changes the bytes), so it is the wrong basis for lineage. Instead each
  video resolves to a stable `lineage_uid` that rides in the file's metadata and
  survives byte churn.

To avoid over-engineering now while staying durable, `lineage_uid` is a **stable
interface with a pluggable generator**, resolved in this order (first hit wins):

1. **minted uid** in mp4 metadata (ULID/uuid4 + `parent_uids`) — *future*, written
   at `VHS_VideoCombine`/post-process; makes videos self-describing. Preferred once
   present.
2. **embedded-prompt hash** — sha256 of the ComfyUI prompt/workflow already
   embedded in every mp4 (the same tag seed-source recovery reads). **Adopt now**:
   works for historical files, no pixel decode, preserved through re-mux. (Identical
   prompt+seed collide — acceptable: same generation.)
3. **decoded-frame signature (video pHash)** — *reserved fallback* for when metadata
   is stripped; transcode-tolerant but requires decoding. Video analogue of the
   image `phash` column.

Registry: add a reserved `lineage_uid` column (like `phash`); `kind=video` rows key
on it, byte `content_id` becomes optional/lazy for video. Lineage edges use
`lineage_uid`/`parent_uids` rather than filename stems. Because (1)→(2)→(3) is a
resolution *order*, we can ship (2) today and add minted uids later without breaking
existing links.

## Phases

### Phase 0 — registry (minimal, under Phase 1)   [done]
`asset_registry.py`: `hash_file`, `register(path, relpath, kind)`, lookups
(`by_content_id`, `by_basename`, `by_relpath`), scoped scan. Only the assets touched
by Phase 1 (shape-family outputs + their source stills) are registered initially; a
full scan comes later.

### Phase 1 — backfill jobs   [done]
`shape_factory.py backfill-jobs --family <slug> [--all-shaped] [--dry-run|--apply]`.
Scope: **known shape families only** (clean `graph_hash`). For each deposit output
with no job:
- reconstruct `bindings` from the embedded prompt/workflow: `source_still` /
  `source_video` (via `LoadImage` / VHS loaders → `input/<name>`), `prompt_profile`
  (positive/negative pulled from the shape's binding node ids, best-effort);
- take `graph_hash`/`shape_id` from the family shape;
- register source + output assets → `content_id`s;
- write a synthetic `.job.json` with `origin:"backfill"`, `status:"completed"`,
  `outputs:[existing file]`, deposit link, `created_at` from file mtime;
- emit `input:<content_id>` → output lineage edges so heuristics see true ancestors.
Factory map pairs a backfill job to its existing deposit output **by relpath** so a
seeded output shows as one source→output pair (no seed/job duplicate).

### Phase 2 — locate (find lost)   [planned]
`assets audit` scans references (job bindings, generated workflows, pools) for broken
paths; strategy ladder: registry `content_id` → exact basename → hash-token fuzzy
(promote `workflow_repair._resolve_missing_asset` to a shared util) → size/pHash.
`assets locate --ref <x> [--apply]` resolves + repairs one reference. Read-mostly.

### Phase 3 — relocate (move)   [planned]
`assets relocate --id <content_id> --to <dest> [--dry-run]`: move the file **and its
companions** (png+mp4+xmp atomically), update registry path + `moved_history`, rewrite
every reference. Dry-run first, always.

**Invariant that makes reorg controlled:** references are hash-anchored, so a
file's location is a mutable pointer. A reorg pass is *verifiable* — correct iff
the set of `content_id`s is unchanged before/after (proves nothing was lost or
orphaned regardless of how many files moved) — and *resumable* — if interrupted,
any asset is still findable by hash (registry → fuzzy → remote), so a re-scan
continues rather than requiring a restore.

### Phase 4 — image reorganization + content dedup   [planned]
Uses 0–3. The same content is scattered as **renamed duplicates** — some names
embed the `sha256`, many do not — so dedup must be **content-based**, never
name-based. The registry's `content_id` (sha256 of bytes) is the canonical unique
id and already unifies all copies regardless of filename. A full-tree scan groups
files by `content_id`; each group keeps one canonical path and the rest become
references (hardlink/symlink or registry pointer), reclaiming space. Then strip
`:Zone.Identifier` ADS + empty scrape dirs, and move images into a **by-usage**
taxonomy — `input/stills/used/` (referenced by an output/job) vs
`input/stills/unused/` (+ `_archive/` for junk) — with full reference rewrite.

## Design notes / decisions
- Identity: exact sha256 now; perceptual hash reserved.
- Storage: SQLite at `output/output/_status/asset_registry.sqlite` (writable in both
  host and container; `.data/` is read-only in the container except the jobs dir).
- Backfill jobs live in `.data/shape_factory/jobs/<family>/` (the RW mount) with an
  `origin:"backfill"` marker and a distinct `__backfill__` job key so they are
  filterable and never collide with live jobs.
- Companion assets (png/mp4/xmp sharing a stem) are always treated as a unit for moves.
