# Appetite `remove` — hide, then delete when nothing depends on it

**Last updated:** 2026-09-08

`remove` is a **terminal appetite mark**, not a fifth step on the `less → fast_track` scale. It means: do not show this asset in normal lists, and do not let it seed or bind factory jobs.

## Operator path

1. Mark **Remove** (`b` / forbidden) on Workbench, Rate queue, Library, or still gallery.
2. The row **disappears from default views**. Workbench’s remove chip starts **off**; turn it on to review.
3. Change appetite (or clear it) to restore the asset to lists and factory eligibility.
4. If the review says **no dependents**, **Delete** removes the file and its ratings.

## What is live now

| Surface | Behavior |
|---------|----------|
| Workbench | Remove chip defaults off (hidden). Turning it on shows marked rows plus a review banner (ref counts, no-dependents vs has-references). Ready rows get **Delete**; **Delete N ready** batches them. |
| Discovery library | Skipped unless `?include_removed=1`. |
| Still gallery | Default / All / Marked / Unmarked omit `remove`. Explicit **Remove (review)** filter lists them. |
| Hourly picks | Stills and recipes with `remove` are dropped (weight 0 / skip). |
| Queue submit | `queue_from_request_body` rejects bindings whose path is `remove` (`ValueError appetite_remove:…`). Existing pending jobs are left alone. |
| Rate sampler | `needs_rating_item` is false for remove-marked clips. |

Storage is the same appetite index as the other states (`appetite_index.json` / `ratings.sqlite`). Aliases `bin` / `discard` / `delete` / `junk` normalize to `remove`.

## Lifecycle review

`GET /api/discovery/asset-remove/review` (Workbench banner when the remove chip is on) scans:

- Appetite rows with `appetite=remove`
- Factory jobs (`.data/shape_factory/jobs/*.job.json`) as **output** vs **source**
- Pool indexes (`.data/pools/*/index.json` `members[].path`)

Per asset:

- **No dependents** (`purge_ready`): not used as a source by any job, not in a pool. The creating job naming it as an output is expected lineage, not a blocker.
- **Has references**: in-flight or historical jobs bind it as a source, and/or it is still a pool member.

## Straightforward delete

`POST /api/discovery/asset-remove/purge` `{ relpath }` or `{ relpaths: [...] }` re-checks the gate, then:

1. Refuses `not_remove` or `has_references` (does not unlink).
2. Deletes the media file and same-stem sidecars (XMP, thumb png/webp/jpg, `.trims.json`, `.metadata.json`).
3. Deletes **quality + appetite** rows in `ratings.sqlite` (the rating itself is a dependency we *do* drop).
4. Archives the producing job (`.job.json` → `.discarded`) when no other videos remain. If the job still has other outputs on disk, those stay and this path is stripped from `submit.outputs` / `deposit.videos`.

Workbench also omits finished jobs whose named output file is already gone, so a reload does not show a row with a missing thumbnail.

Not deleted in this pass: pool members, downstream source bindings, lineage edges, discovery-index rows (those drop on the next scan once the file is gone).

See also the two-axis split in [`RATINGS_V1_PLAN.md`](./RATINGS_V1_PLAN.md).
