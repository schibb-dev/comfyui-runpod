# Experiments UI API concurrency (review later)

**Status:** Parked — do **not** add more worker threads as the next performance move. Revisit only if Workbench / Queue still freeze after the staged load path.

**Related:** [`SCALE_INDEX_ARCHITECTURE.md`](./SCALE_INDEX_ARCHITECTURE.md) (indexes vs walking `.job.json`), [`STILL_TAG_INDEX_HOUR_PLAN.md`](./STILL_TAG_INDEX_HOUR_PLAN.md), Workbench `lite=1` / `still_tags_only=1` in `scripts/experiments_ui_server.py`.

---

## What is already true

`ExperimentsServer` is a `ThreadingHTTPServer` (`scripts/experiments_ui_server.py`). Each HTTP request gets a thread. The process is still **one CPython interpreter** inside `comfyui0-runpod` (uid 1000).

More threads will **not** make a single fat handler faster. They also do **not** stop that handler from starving siblings.

---

## Why Queue / other `/api/*` stall during a Workbench load

Cold `GET /api/shape-factory/work-products` walks thousands of factory job JSON files on a **WSL bind mount** (`stat` + `json.loads` in a tight Python loop). That work is:

1. **GIL-bound** — other request threads cannot run Python while the loop holds the GIL.
2. **Disk-serialized** — even when the GIL drops on I/O, the bind-mount filesystem does not like many concurrent walks of the same tree.

Observed: `/api/queue` hung behind a work-products GET (timeouts / 0-byte responses) even though the server is already threaded.

A second overlapping fat walk (full enrich + still-tag attach) made this worse. Frontend **must sequence** stages (lite list → Comfy history enrich → still-tag stubs) so two scans do not fight the same disk.

---

## What we chose instead of “make it multi-threaded”

| Stage | Endpoint flags | Purpose |
|-------|----------------|---------|
| Paint jobs first | `lite=1` | Skip Comfy `/history`, skip still-tag attach, skip reconcile persist, shorter Comfy `/queue` timeout, mtime-prefix job scan |
| Enrich | default GET after lite | Queue + history in a small `ThreadPoolExecutor`; still no still-tag attach |
| Still-tags last | `still_tags_only=1` | Stub rows only (counts/status; no catalog-per-target, no `scope_json`) |

In-process cache TTL is **12s from store time** (completion), not request start — a 30s scan used to miss its own cache forever.

`attach_live_comfy_queue(..., locate_jobs=False, still_tag_lookup=False)` on lite avoids reading every `.job.json` just to label the live Comfy queue.

---

## When to revisit (decision checklist)

Reopen this doc only if **after** the staged path, interactive `/api/queue` still waits on Workbench.

Then the next moves are **not** `ThreadingHTTPServer` knobs:

1. **Job index as the list source** — `job_output_index.sqlite` / mtime ledger so Workbench does not parse ~3k JSON files per GET ([`SCALE_INDEX_ARCHITECTURE.md`](./SCALE_INDEX_ARCHITECTURE.md)).
2. **Separate process** for the factory scan (multiprocessing or a tiny sidecar) if an index is still too much and GIL starvation is the measured bottleneck.
3. **Do not** asyncio-rewrite the stdlib server unless the pain is many idle sockets, not disk loops.
4. **Do not** add a thread pool around the job walk hoping for speedup on one request.

GPU / Comfy stays one consumer; this note is only the Experiments UI Python process.

---

## How to measure

From the host (Vite proxies `/api` → container `:8790`):

```bash
# While Workbench is loading, a second client should still return quickly.
curl -sS -m 5 http://127.0.0.1:8790/api/queue | head -c 80
```

Payload timings: `load_ms` / `load_phases` on `/api/shape-factory/work-products` (`comfy_queue`, `factory_jobs`, `queue_attach`, …). Still-tag stubs should be a handful of milliseconds when `still_tags_only=1`.
