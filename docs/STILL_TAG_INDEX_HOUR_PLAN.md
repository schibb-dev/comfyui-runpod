# Still-tag index hour — plan

**Status:** Active (2026-08-28). **IH1 + gallery demo** landed (enqueue≠drain, schedule,
front drain, Still gallery index-hour panel, dry-run smoke).  
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
2. **Reserved window (“index hour”).** A schedule defines when the drainer may burn the
   backlog on the configured Comfy. Early backlog days may be multi-hour windows
   (“index evening”) — same mechanism, longer duration.
3. **Front-of-queue refill, circumspect depth.** During the window, tag prompts are
   submitted with Comfy `front: true` so they outrank normal I2V, **but** we cap how many
   tag prompts are in flight / piled so we do not dump the entire backlog ahead of
   everything forever in one shot.
4. **Same job body.** Local concert Comfy or RunPod Comfy — `VISION_COMFY_SERVER` /
   schedule override only. No second tagger product.
5. **Not shape-factory Work Products.** Tagging stays still-tag runs/events, not `.job.json`
   families (see parent plan).

---

## Tunable knobs (policy, not schema)

| Knob | Purpose |
|------|---------|
| `enabled` | Master switch for schedule-gated drain |
| `window_start` + `window_duration_min` | Local clock window (multi-hour OK) |
| `timezone` | Interpret start (default host / explicit IANA) |
| `front` | Submit tag prompts to front of Comfy queue (default **true** in window) |
| `max_inflight` | Max concurrent outstanding Florence prompts (start **1**) |
| `max_items_per_tick` / drain `--max-items` | Cap work per drain invocation |
| `comfy_server` | Optional override (else env / `127.0.0.1:8188`) |
| `auto_drain_on_enqueue` | Escape hatch for smokes (default **false**) |

Future (not required for first slice): hard “pause I2V” gate; urgency `drain_now` for a
single still; RunPod spin/tear recipes; V2 shared worker claiming the same queued runs.

---

## Schedule store

**Path:** `<data_root>/shape_factory/still_tag_schedule.json`

```json
{
  "schema_version": 1,
  "enabled": false,
  "timezone": "America/New_York",
  "window_start": "02:00",
  "window_duration_min": 180,
  "front": true,
  "max_inflight": 1,
  "max_items_per_tick": 48,
  "comfy_server": null,
  "auto_drain_on_enqueue": false
}
```

Drainer (CLI or API kick) loads this file, checks `in_window`, applies knobs. Cron or an
Experiments tick can call `vision_still_tag_drain.py --respect-schedule` every minute;
outside the window it no-ops.

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

- Home / Experiments schedule card (mirror hourly controls)
- Optional hard pause of I2V during window
- Documented RunPod drain recipe
- Live GPU smoke of events while draining (optional; dry-run path covers UI)

---

## Success criteria

- [x] Gallery tag actions only grow the backlog by default (no surprise Florence mid-I2V) — IH1 enqueue policy
- [x] Index-hour drain can front-load Florence prompts with an in-flight/item cap — CLI/API drain (`max_inflight` recorded; sequential wait in IH1)
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
