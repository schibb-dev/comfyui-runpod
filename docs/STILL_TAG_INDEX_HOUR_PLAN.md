# Still-tag index hour — plan

**Status:** Active (2026-09-19). **SLA sessions** — attempt queued stills within
`max_wait_hours` (default **3h**; gallery Queue tag uses `manual_max_wait_hours`,
default **1h**). Exclusive Florence burst aims at `session_minutes` (default **15**);
hourlies paused, Comfy parked to pending. The session is a start/budget **target**;
in-flight tagging runs finish unless they exceed `kill_after_min` (default **60**).
All knobs live in `still_tag_schedule.json` and the gallery Tagging backlog panel.
**Parent:** [`STILL_AUTO_TAGGER_PLAN.md`](./STILL_AUTO_TAGGER_PLAN.md) (T1 store/UI enqueue already landed).

**Related:** [`SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md`](./SCHEDULED_AND_CONTAINER_JOBS_RUNDOWN.md),
[`HOURLY_UTILITY_PLAN.md`](./HOURLY_UTILITY_PLAN.md) (cadence UI patterns),
[`RUNPOD.md`](../RUNPOD.md).

---

## Problem

Florence PromptGen-large and the I2V / video stack fight for GPU residency. Interleaving
tag prompts with Kneel/GEX causes repeated model swaps (or VRAM thrash). Gallery still
needs to **enqueue** tagging anytime; the GPU work should not start on every click.

There is also a **large initial backlog** of untagged stills. The first “index hour” may
legitimately run for several hours — that is a deliberate drain, not accidental starvation
of video, if the window and in-flight caps are explicit.

---

## Locked ops model

```text
Gallery / CLI  →  enqueue only (SQLite still_tag_runs)     [anytime]
                      ↓
Index-hour drainer →  Comfy /prompt Florence (prefer front) [reserved window]
                      ↓
                 provisional tags + events
```

1. **Enqueue ≠ drain.** UI/API default is backlog-only. No Florence in the request path;
   no automatic GPU kick on enqueue (unless an explicit opt-in flag/env for smoke).
2. **SLA session (default) or clock window.** A periodic tick (default
   `evaluate_interval_min` **15**) scans input (`scan_interval_min` **15**) for
   new stills and evaluates SLAs. `mode=sla`: start a drain when the oldest
   **manual** request has waited `manual_max_wait_hours` (1) or the oldest bulk
   backlog still has waited `max_wait_hours` (3). Each session aims at
   `session_minutes` (15) of Florence — batch size is scaled from recent
   seconds/still (cap 32). Manual runs are drained first. `mode=clock` keeps a
   fixed local window. After a session, wait `resume_gap_min` (20) unless the
   SLA is already overdue. Empty ticks are a brief no-op (no GPU occupy).
   A started run is not cut at the session target; `/interrupt` only after
   `kill_after_min` (60). All of these numbers are adjustable. LoadImage
   `input_ref` is used when the file is under Comfy's input bind; otherwise
   the still is uploaded. HTTP 400 / bad-ref errors retry once via upload.
   Empty queued runs are cancelled and never occupy the GPU.
3. **Exclusive GPU occupancy (model-set class).** A job’s class is the **set of large
   models it needs**. When a Florence tagging job *starts*, park every Comfy queue item
   whose large-model set is not that Florence set (interrupt+park if one is running).
   Same rule if Wan I2V or CLIP embed starts. Related = **exact same set** (not subset:
   `{CLIP-H}` is not related to `{Wan, CLIP-H}`). GPU is available again after that set
   finishes and `/free`. Clock windows may start a set; they do not share VRAM.
   CLIP embed backfill declares `{CLIP-ViT-H-…}` and parks the whole Comfy queue.
4. **Same job body.** Local concert Comfy or RunPod Comfy — `VISION_COMFY_SERVER` /
   schedule override only. No second tagger product.
5. **Not shape-factory Work Products.** Tagging stays still-tag runs/events, not `.job.json`
   families (see parent plan).

---

## Tunable knobs (policy, not schema)

| Knob | Purpose |
|------|---------|
| `enabled` | Master switch for schedule-gated drain |
| `mode` | `sla` (default) or `clock` |
| `max_wait_hours` | SLA: attempt bulk/untagged backlog within this many hours (default **3**) |
| `manual_max_wait_hours` | SLA: attempt gallery **Queue tag** (selected stills) within this many hours (default **1**) |
| `scan_interval_min` | How often the tick scans input for new stills (default **15**) |
| `evaluate_interval_min` | How often the tick evaluates SLAs and may start a session (default **15**) |
| `auto_enqueue_untagged` | On each scan, enqueue newest untagged stills (default **true**) |
| `auto_enqueue_limit` | Max stills to enqueue per scan (default **96**) |
| `session_minutes` | Target exclusive session length (default **15**); runs already started finish |
| `kill_after_min` | Hard cap: interrupt Comfy and requeue remainder (default **60**) |
| `resume_gap_min` | Minimum idle after a session before the next (default **20**) |
| `sec_per_still` | Fallback seconds/still for batch scale (live median from recent done runs) |
| `occupy_gpu` | Pause hourlies + park Comfy/ledger for the session (default **true**; skipped on dry-run) |
| `window_start` + `window_duration_min` | Clock mode only; SLA uses `session_minutes` as the target |
| `timezone` | Interpret start (default host / explicit IANA) |
| `front` | Submit tag prompts to front of Comfy queue (default **true** in window) |
| `max_inflight` | Max concurrent outstanding Florence prompts (start **1**) |
| `max_items_per_tick` / drain `--max-items` | Cap work per drain invocation |
| `comfy_server` | Optional override (else env / `127.0.0.1:8188`) |
| `auto_drain_on_enqueue` | Escape hatch for smokes (default **false**) |

Future polish: occupancy-state JSON + UI (“GPU: tagging / generation / embed / idle”);
urgency `drain_now` for a single still; RunPod spin/tear recipes; V2 shared worker
claiming the same queued runs. **Hard pause of I2V is no longer future** — it is the
locked occupancy rule (park + `/free` before Florence).

---

## Schedule store

**Path:** `<data_root>/shape_factory/still_tag_schedule.json`

```json
{
  "schema_version": 1,
  "enabled": false,
  "timezone": "America/New_York",
  "mode": "sla",
  "max_wait_hours": 3,
  "manual_max_wait_hours": 1,
  "scan_interval_min": 15,
  "evaluate_interval_min": 15,
  "auto_enqueue_untagged": true,
  "auto_enqueue_limit": 96,
  "session_minutes": 15,
  "kill_after_min": 60,
  "resume_gap_min": 20,
  "sec_per_still": 12,
  "occupy_gpu": true,
  "window_start": "03:00",
  "window_duration_min": 15,
  "front": true,
  "max_inflight": 1,
  "max_items_per_tick": 96,
  "comfy_server": null,
  "auto_drain_on_enqueue": false
}
```

Drainer (CLI or API kick) loads this file, checks SLA-due / `in_window`, applies knobs.
Lease sidecar: `<data_root>/shape_factory/still_tag_session.json`. Cron or an
Experiments tick can call `vision_still_tag_drain.py --respect-schedule` every minute;
when not due it no-ops. Stale leases older than 90 minutes are recovered.

---

## API / CLI (first slice)

| Surface | Role |
|---------|------|
| `POST …/stills/tag` | Enqueue only (unless `drain_now` or schedule `auto_drain_on_enqueue`) |
| `GET …/stills/tag/backlog` | Queued runs, target counts, schedule + `in_window` |
| `GET/POST …/stills/tag/schedule` | Read/update schedule JSON |
| `POST …/stills/tag/drain` | Drain tick (`sync: true` for demo; else background). Respects schedule unless `force` |
| `vision_still_tag_drain.py` | Ops drain: `--respect-schedule` \| `--force`, `--front`, `--max-items`, `--until-minutes` |
| `vision_still_tag_index_hour_smoke.py` | Dry-run enqueue → force drain (no GPU) |

CLI debug `vision_still_tag_run.py` remains for one-shot smoke; prefer
`--enqueue-only` without GPU, then drain separately.

---

## Phased movement

### IH0 — This doc + contracts — **done**

Lock enqueue≠drain, schedule knobs, front+inflight story.

### IH1 — Implement slice — **done**

- Schedule load/save + `in_window`
- Enqueue stops auto-kicking by default
- Comfy runner supports `front`
- `drain_backlog` / CLI drain with front + max-items + until + respect-schedule
- Backlog + schedule GET/POST; drain POST kicks background drain tick
- Unit tests (schedule window, enqueue-without-kick, front payload)

### IH1.5 — Gallery demo — **done**

- Still gallery **Index hour** panel: backlog counts, window status, schedule enable,
  Drain now (dry-run / Comfy), “queued for index hour” copy
- Drain API `sync: true` for reliable dry-run demos
- Smoke script + unit test for enqueue → force dry-run drain

### IH2 — Measure

- Real tags/min with `keep_model_loaded`, `max_inflight` 1→2
- Choose steady `window_duration_min` / `max_items_per_tick` for backlog weeks

### IH3 — Ops polish

- Occupancy lease (park generation + `/free`) before Florence drain — required, not optional
- Home / Experiments schedule card (mirror hourly controls)
- Documented RunPod drain recipe
- Live GPU smoke of events while draining (optional; dry-run path covers UI)

---

## Success criteria

- [x] Gallery tag actions only grow the backlog by default (no surprise Florence mid-I2V) — IH1 enqueue policy
- [x] Index-hour drain can front-load Florence prompts with an in-flight/item cap — CLI/API drain (`max_inflight` recorded; sequential wait in IH1)
- [x] Tagging drain only runs while holding the tagging GPU lease (hourlies paused, Comfy parked to pending; no I2V interleave) — skipped on dry-run
- [x] Schedule knobs changeable without schema migration
- [x] Multi-hour windows work (backlog burn) without new code paths
- [x] Gallery shows backlog / window / drain controls (demo without GPU via dry-run)
- [ ] Same SQLite runs/events UI polling still works while draining on live Florence GPU

---

## Suggested first commands (after IH1)

```bash
# Enqueue → force drain (dry-run, no GPU) — demo path
python3 workspace/scripts/vision_still_tag_index_hour_smoke.py --limit 3

# Enqueue a smoke batch (no GPU)
python3 workspace/scripts/vision_still_tag_run.py --enqueue-only --limit 12 --dry-run

# Drain now (ignore schedule), front of Comfy queue
python3 workspace/scripts/vision_still_tag_drain.py --force --front --max-items 12 \
  --comfy-server http://127.0.0.1:8188

# Cron-style: only inside configured window
python3 workspace/scripts/vision_still_tag_drain.py --respect-schedule
```
