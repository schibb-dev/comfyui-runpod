# Factory job status lifecycle

How a shape-factory `job.json` `submit.status` moves, and what Workbench should
do with each state. Comfy `/queue` is canonical while a prompt is live.
History is a short window. **Files on disk win** after that window closes.

## States

| Status | Meaning | Next |
|---|---|---|
| `pending` | Job exists, not submitted (or unqueued back to edit). Factory FIFO (`submit.pending_rank`, 0 = next drain). Workbench ▲▼ reorders it. Hourly floor (`pending_hourly_min`, default 5) stays on this queue; drain refills if the hourly count dips and only submits overflow / Submit Queue jobs. | Drain / Now → `queued`. Discard if unused. |
| `queued` | Prompt is in Comfy waiting. | Starts → `running`. Unqueue → `pending`. |
| `running` | Prompt is the current Comfy execution. | Success → `complete`. Error → `error`. Cancel / Comfy dies with no file → `interrupted`. |
| `complete` | Finished video exists (Comfy history **or** filesystem). | Deposit into the family pool. |
| `error` | Comfy recorded an execution failure (no usable output). | Archive / delete, or replay a new job. OOM extends may auto-retry shorter. |
| `interrupted` | Prompt left `/queue` **and** `/history` **and there is no output file**. Typical causes: Comfy restart, queue clear, operator interrupt. | If the queue ledger restores it → rebound to `queued`/`running` (new `prompt_id`). If an mp4 is found later → heal to `complete`. Otherwise archive / delete / replay. |
| `abandoned` | Submit retries exhausted (never a successful `/prompt`). | Archive / delete. |

`completed` is a legacy alias of `complete`.

## Interrupted is not “failed with a video”

Workbench used to show many **finished** rows as interrupted. That was a
mislabelling:

1. The job finished and wrote `output/<prefix>_00001.mp4`.
2. Comfy dropped the prompt from history (restart or history cap).
3. Reconcile looked for the file under the factory `.data` tree, found nothing,
   and wrote `interrupted_reason=missing_from_comfy_queue_and_history`.

Reconcile now searches the Comfy output bind as well. An interrupted row that
already has an mp4 is promoted to `complete` (`healed_from=interrupted`) and
can be deposited.

True interrupts have **no** job output. The source still / parent clip in the
preview is not a result.

## Operator paths

- Workbench load runs `reconcile_inflight_jobs_with_comfy` (heal + ledger rebound).
- CLI: `python3 workspace/scripts/shape_factory.py jobs heal-interrupted --jobs-dir .data/shape_factory/jobs --deposit`
- Archive on an interrupt soft-renames to `.discarded` (forensics; no restore UI).
- Bulk “clear failed” deletes `error` / `failed` / `interrupted` / `abandoned`
  in the current filter — only use it on rows with no output.

## Future review

- **Family swap** (`POST /api/shape-factory/swap-family`) is still a useful
  retarget (replay as another family, retire the old queued job). The Workbench
  **Jobs list Swap** control is hidden (2026-09-08) because that UX was rough —
  review the surface and, if needed, the replay-then-retire mechanics before
  putting bulk swap back.

## Do not

- Treat `missing_from_comfy_queue_and_history` as proof the render failed.
- Delete interrupted rows that still show a result video — heal/deposit first.
- Confuse ledger restore (new `prompt_id`, same `job_key`) with a new job.
