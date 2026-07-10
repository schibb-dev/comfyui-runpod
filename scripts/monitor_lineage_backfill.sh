#!/usr/bin/env bash
# Emit lineage backfill progress + ETA for loop monitoring.
set -euo pipefail
LOG=/tmp/lineage_backfill.log
EDGES=/home/yuji/comfyui-runpod-data/output/_status/discovery_lineage_edges.json
HISTORY=/tmp/lineage_progress_history.jsonl
TOTAL=13260

python3 <<'PY'
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

LOG = Path("/tmp/lineage_backfill.log")
EDGES = Path("/home/yuji/comfyui-runpod-data/output/_status/discovery_lineage_edges.json")
HISTORY = Path("/tmp/lineage_progress_history.jsonl")
TOTAL = 13260
now = datetime.now(timezone.utc)

text = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
progress_vals = [int(m.group(1)) for m in re.finditer(r"progress (\d+)/13260", text)]
latest = progress_vals[-1] if progress_vals else 0
done_m = re.search(r"\[backfill\] done rows_ok=(\d+)", text)
if done_m:
    latest = max(latest, int(done_m.group(1)))

running = subprocess.run(
    ["pgrep", "-f", "backfill_discovery_lineage.py"],
    capture_output=True,
    text=True,
).stdout.strip()
state = "running" if running else ("complete" if done_m else "stopped")

edges_bytes = EDGES.stat().st_size if EDGES.exists() else 0
tail = ""
for line in text.splitlines():
    if "progress" in line or "done" in line or "FAIL" in line:
        tail = line

# Append sample for rate tracking
sample = {"ts": now.isoformat(), "progress": latest, "state": state}
HISTORY.parent.mkdir(parents=True, exist_ok=True)
with HISTORY.open("a", encoding="utf-8") as f:
    f.write(json.dumps(sample) + "\n")

# Load recent history (last 24h, cap 200 lines)
samples = []
if HISTORY.exists():
    for line in HISTORY.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
        try:
            s = json.loads(line)
            s["ts"] = datetime.fromisoformat(s["ts"].replace("Z", "+00:00"))
            samples.append(s)
        except Exception:
            pass

def fmt_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # nan
        return "unknown"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"

def rate_and_eta(window_samples):
    if len(window_samples) < 2:
        return None, None, None
    a, b = window_samples[0], window_samples[-1]
    dt = (b["ts"] - a["ts"]).total_seconds()
    dp = b["progress"] - a["progress"]
    if dt <= 0 or dp <= 0:
        return None, None, None
    rate_per_min = dp / dt * 60
    remaining = TOTAL - latest
    eta_sec = remaining / (dp / dt) if dp > 0 else None
    return rate_per_min, eta_sec, b["ts"]

rate_recent, eta_recent, _ = rate_and_eta(samples[-3:] if len(samples) >= 3 else samples[-2:])
rate_session, eta_session, session_start = rate_and_eta(samples[:1] + [samples[-1]] if samples else [])

# Process elapsed from pgrep
elapsed_sec = None
if running:
    pid = running.splitlines()[0].strip()
    try:
        out = subprocess.check_output(["ps", "-p", pid, "-o", "etimes="], text=True).strip()
        elapsed_sec = int(out)
    except Exception:
        pass

# Fallback: use progress/elapsed when history too thin
if rate_session is None and elapsed_sec and latest > 0:
    rate_session = latest / elapsed_sec * 60
    eta_session = (TOTAL - latest) / (latest / elapsed_sec)

# Prefer recent window once we have >=2 ticks 10m apart; else session average
rate_used = rate_recent if rate_recent else rate_session
eta_used = eta_recent if eta_recent else eta_session
eta_label = "recent" if eta_recent else "session_avg"

# Stuck detection: running but no progress for 15+ minutes
stuck = False
stuck_reason = None
if state == "running" and samples:
    window_start = now - timedelta(minutes=15)
    recent = [s for s in samples if s["ts"] >= window_start]
    if len(recent) >= 2:
        dp_window = recent[-1]["progress"] - recent[0]["progress"]
        if dp_window <= 0:
            stuck = True
            stuck_reason = "no_progress_15m"
    elif LOG.exists():
        log_age_sec = now.timestamp() - LOG.stat().st_mtime
        if log_age_sec > 900 and latest > 0:
            stuck = True
            stuck_reason = "log_stale_15m"

health = "complete" if state == "complete" else ("stuck" if stuck else ("stopped" if state == "stopped" else "ok"))

payload = {
    "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "state": state,
    "health": health,
    "stuck": stuck,
    "stuck_reason": stuck_reason,
    "progress": latest,
    "total": TOTAL,
    "pct": f"{100 * latest / TOTAL:.1f}",
    "remaining": TOTAL - latest,
    "edges_bytes": edges_bytes,
    "elapsed": fmt_duration(elapsed_sec) if elapsed_sec is not None else None,
    "rate_per_min_recent": round(rate_recent, 2) if rate_recent else None,
    "rate_per_min_session": round(rate_session, 2) if rate_session else None,
    "rate_per_min": round(rate_used, 2) if rate_used else None,
    "eta_basis": eta_label if eta_used else None,
    "eta": fmt_duration(eta_used) if eta_used else None,
    "eta_finish_utc": (now + timedelta(seconds=eta_used)).strftime("%Y-%m-%dT%H:%M:%SZ") if eta_used else None,
    "eta_recent": fmt_duration(eta_recent) if eta_recent else None,
    "eta_session": fmt_duration(eta_session) if eta_session else None,
    "eta_finish_utc_recent": (now + timedelta(seconds=eta_recent)).strftime("%Y-%m-%dT%H:%M:%SZ") if eta_recent else None,
    "eta_finish_utc_session": (now + timedelta(seconds=eta_session)).strftime("%Y-%m-%dT%H:%M:%SZ") if eta_session else None,
    "tail": tail,
}
print(json.dumps(payload))
PY
