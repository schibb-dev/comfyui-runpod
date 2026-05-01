# Scheduled jobs and container services – rundown

Current state as of this doc: you have **both** Windows Scheduled Tasks **and** Docker containers (with the `ops` profile) doing the same jobs. The three scheduled tasks are **redundant** when Docker + ops is running.

---

## 1. Windows Scheduled Tasks (comfyui-runpod)

All three run against **http://127.0.0.1:8188** (or, for the report task, by running a script inside the watch_queue container). Scripts live under `scripts\*.ps1` and call `workspace\scripts\*.py`.

| Task | Schedule | What it does | Useful? |
|------|----------|--------------|---------|
| **ComfyUI-QueueIncompleteExperiments** | Every **10 min** | Runs `queue_incomplete_experiments.py --server http://127.0.0.1:8188`. Clears `submit.json` for runs no longer in the ComfyUI queue and runs the experiment queue manager once so eligible incomplete runs can be re-queued. | **Redundant** if Docker `queue_incomplete_experiments` container is running (see below). |
| **ComfyUI-RefreshRunStatus** | Every **1 min** | Runs `refresh_run_status.py --server http://127.0.0.1:8188`. Writes/updates `status.json` per run from ComfyUI `/queue` and on-disk artifacts (history.json, submit.json) so you see done/running/queued/submitted without waiting for history. | **Redundant** if Docker `refresh_run_status` container is running. |
| **ComfyUI-ReportExperimentQueueStatus** | Every **1 min** | Runs **inside** container `comfyui0-watch-queue` via `docker exec`: `report_experiment_queue_status.py --server http://comfyui:8188 --newest-first --limit 10 --summary-only`. Appends a timestamped summary to `workspace/output/output/experiments/_status/queue_status.log`. | **Redundant** if Docker `report_experiment_queue_status` container is running (that container already appends to the same log every 60s). |

**Conclusion:** If you always run Docker with `docker compose --profile ops up`, these three tasks don’t add anything. You can disable or delete them to avoid duplicate work and log noise. Keep them only if you sometimes run **without** Docker (e.g. ComfyUI Windows portable only) and still want queue/status/report behavior on a schedule.

---

## 2. Docker Compose services

### Always-on (no profile)

| Service | Container | What it does |
|---------|-----------|--------------|
| **comfyui** | `comfyui0-runpod` | Main ComfyUI server (entrypoint: setup + `python3 main.py`). Serves UI and API on 8188, runs workflows, mounts workspace/models/credentials. Optional: Experiments UI, Krita AI downloads, model aliases, CivitAI downloader. |
| **watch_queue** | `comfyui0-watch-queue` | Runs **watch_queue.py** in a loop: watches `workspace/output/output/experiments`, submits `prompt.json` to ComfyUI, polls for `history.json`, writes `submit.json`. Keeps experiment runs flowing into the queue. **Essential** for automated experiment runs. |

### Ops profile (`docker compose --profile ops up`)

When you bring up with the `ops` profile, these **additional** containers run:

| Service | Container | What it does |
|---------|-----------|--------------|
| **refresh_run_status** | `comfyui0-refresh-run-status` | Loop: every **60s** runs `refresh_run_status.py --server http://comfyui:8188`, then sleeps. Same as the scheduled task “ComfyUI-RefreshRunStatus” but inside Docker. |
| **report_experiment_queue_status** | `comfyui0-report-queue-status` | Loop: every **60s** runs `report_experiment_queue_status.py` (summary to stdout and into `_status/queue_status.log`), then sleeps. Same as the scheduled task “ComfyUI-ReportExperimentQueueStatus”. |
| **queue_incomplete_experiments** | `comfyui0-queue-incomplete` | Loop: every **600s** (10 min) runs `queue_incomplete_experiments.py --server http://comfyui:8188`, then sleeps. Same as the scheduled task “ComfyUI-QueueIncompleteExperiments”. |
| **queue_ledger** | `comfyui0-queue-ledger` | Long-running `comfy_queue_ledger.py`: passively polls ComfyUI `/queue`, writes a best-effort shadow ledger (`_status/comfy_queue_ledger_state.json` + `comfy_queue_ledger.jsonl`), performs startup restore with attempt caps/cooldown, and (optionally) applies gentle spillover/refill to keep pending depth near target. Uses normal/churn pacing + breaker to avoid loops/churn. |
| **ws_event_tap** | `comfyui0-ws-event-tap` | Long-running **ws_event_tap.py**: connects to ComfyUI’s WebSocket, records execution timings (execution_start, executing, execution_success/error/interrupted) per `prompt_id`, maps to experiment run dirs via `submit.json`/`metrics.json`, and merges timing into `<run_dir>/metrics.json`. **Not** duplicated by any scheduled task; only runs in Docker when ops profile is on. |

---

## 3. Script purposes (one-line)

| Script | Purpose |
|--------|---------|
| **watch_queue.py** | Submits experiment runs to ComfyUI, polls for history, writes submit.json/history.json. |
| **queue_incomplete_experiments.py** | Cleans submit.json for runs no longer in queue; runs queue manager once to re-queue eligible incompletes. |
| **comfy_queue_ledger.py** | Best-effort queue shadow + startup restore + optional spillover/refill; non-ACID by design; prioritizes anti-loop/anti-stuck behavior. |
| **refresh_run_status.py** | Writes/updates `status.json` per run from /queue + on-disk state (done/running/queued/submitted). |
| **report_experiment_queue_status.py** | Prints/appends a short queue status report (newest-first, limit 10, summary-only) to a log file. |
| **ws_event_tap.py** | WebSocket client; records per-prompt execution timings into run dir `metrics.json`. |

---

## 4. What’s useful vs redundant

- **Essential for automated experiments:**  
  - **comfyui** (server)  
  - **watch_queue** (feeds the queue)

- **Useful for visibility and recovery:**  
  - **refresh_run_status** (status.json)  
  - **queue_incomplete_experiments** (re-queue stuck incompletes)  
  - **report_experiment_queue_status** (human-readable log)  
  - **ws_event_tap** (execution timings in metrics.json)

- **Redundant when Docker + ops is running:**  
  - All three Windows scheduled tasks (ComfyUI-QueueIncompleteExperiments, ComfyUI-RefreshRunStatus, ComfyUI-ReportExperimentQueueStatus). They repeat what the three ops containers already do.

---

## 5. Recommendations

1. **If you always use Docker with ops:**  
   Disable or remove the three ComfyUI scheduled tasks to avoid duplicate work and duplicate log lines.

2. **If you sometimes run ComfyUI without Docker (e.g. portable):**  
   Keep the two tasks that call 127.0.0.1:8188 (QueueIncompleteExperiments, RefreshRunStatus). The report task (docker exec into watch_queue) is only useful when the watch_queue container is running; otherwise it will fail every run.

3. **GPU monitor** is separate: it runs as a process (or scheduled task “ComfyUI_Enhanced_GPU_Monitor” if installed). It’s not part of docker-compose or these three ComfyUI tasks.

4. **To disable the three tasks (PowerShell as Administrator):**  
   ```powershell
   Disable-ScheduledTask -TaskName "ComfyUI-QueueIncompleteExperiments"
   Disable-ScheduledTask -TaskName "ComfyUI-RefreshRunStatus"
   Disable-ScheduledTask -TaskName "ComfyUI-ReportExperimentQueueStatus"
   ```  
   To remove them entirely:  
   `Unregister-ScheduledTask -TaskName "ComfyUI-QueueIncompleteExperiments"` (and same for the other two).

---

## 6. Queue ledger visibility and control

- Ledger files:
  - `workspace/output/output/experiments/_status/comfy_queue_ledger_state.json`
  - `workspace/output/output/experiments/_status/comfy_queue_ledger.jsonl`
- API visibility from Experiments UI backend:
  - `GET /api/queue/ledger-status` (mode, paused, breaker, backlog count, stats)
- API control actions:
  - `POST /api/queue/ledger-control` with `{"action":"pause"}`
  - `POST /api/queue/ledger-control` with `{"action":"resume"}`
  - `POST /api/queue/ledger-control` with `{"action":"drain-once"}`
  - `POST /api/queue/ledger-control` with `{"action":"reset-breaker"}`
- Key env knobs in `docker-compose.yml` (`queue_ledger` service):
  - `LEDGER_PENDING_TARGET`
  - `LEDGER_SPILLOVER_ENABLED`
  - `LEDGER_MAX_RESTORE_ATTEMPTS`
  - `LEDGER_RESTORE_COOLDOWN_S`
  - `LEDGER_BREAKER_FAILURE_THRESHOLD`, `LEDGER_BREAKER_WINDOW_S`, `LEDGER_BREAKER_OPEN_S`
