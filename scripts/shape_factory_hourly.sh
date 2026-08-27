#!/usr/bin/env bash
# Shape-factory maintenance + optional fill, gated by hourly-schedule.json.
# Pending drain (shape_factory_pending_drain) owns pushing pending onto Comfy when
# this tick leaves jobs pending (Comfy full / submit_mode=pending).
# Priority when filling: occasionally GEX2→FACIAL (HOURLY_FACIAL_DRAIN_EVERY, default
# every 6th cursor), then i2v→FB9_GEX on a steadier cadence (HOURLY_I2V_GEX_DRAIN_EVERY,
# default every 3rd cursor — Kneel/BounceDance/FaceBlast/…), else seed.
# Image seeds weight X-KNEEL-FB9 highest. HOURLY_SEED_OVER_CHAIN_SHARE (default 0.50)
# can still skip facial on a facial-cadence tick. HOURLY_FACIAL_LOOKBACK_DAYS (default 14)
# ignores ancient GEX2 jobs for facial drain.
# sample_cursor advances on every fill tick (chain or seed) so cadences re-roll.
# After hourly policy changes, dry-run variety with:
#   python3 workspace/scripts/shape_factory_hourly.py simulate-picks --count 32
# i2v hourlies target ~90% fresh input stills from the last week (see HOURLY_* below).
# Extension seeds: FB9_GEX and FB9_GEX2 share equal weight (Kneel→GEX2 chain still unused;
# i2v still drains into FB9_GEX).
# Install timer: bash scripts/install-shape-factory-hourly.sh
set -euo pipefail

REPO="${REPO:-/home/yuji/src/comfyui-runpod}"
SCRIPTS="$REPO/workspace/scripts"
LOG="${LOG:-$REPO/.data/shape_factory/hourly.log}"
STATE="${STATE:-$REPO/.data/shape_factory/hourly-state.json}"
SCHEDULE="${SCHEDULE:-$REPO/.data/shape_factory/hourly-schedule.json}"
JOBS_DIR="${JOBS_DIR:-$REPO/.data/shape_factory/jobs}"
CHAIN_MANIFEST="${CHAIN_MANIFEST:-$REPO/.data/chains/best-examples.chain.yaml}"
COMFY="${COMFY:-http://127.0.0.1:8188}"
ADVANCE_CHAIN="${ADVANCE_CHAIN:-1}"
DEV_CHAIN="${DEV_CHAIN:-0}"
HOURLY_PREDICTED_SHARE="${HOURLY_PREDICTED_SHARE:-0.35}"
HOURLY_FRESH_STILL_SHARE="${HOURLY_FRESH_STILL_SHARE:-0.90}"
HOURLY_RECENT_STILL_DAYS="${HOURLY_RECENT_STILL_DAYS:-7}"
HOURLY_SEED_OVER_CHAIN_SHARE="${HOURLY_SEED_OVER_CHAIN_SHARE:-0.50}"
HOURLY_FACIAL_LOOKBACK_DAYS="${HOURLY_FACIAL_LOOKBACK_DAYS:-14}"
# Drain at most one GEX2→FACIAL job every N sample_cursor values (default 6).
HOURLY_FACIAL_DRAIN_EVERY="${HOURLY_FACIAL_DRAIN_EVERY:-6}"
# Drain at most one i2v/still → FB9_GEX job every N sample_cursor values (default 3).
HOURLY_I2V_GEX_DRAIN_EVERY="${HOURLY_I2V_GEX_DRAIN_EVERY:-3}"
# Status walks every complete job with ffprobe; skip by default so fills stay on cadence.
HOURLY_SKIP_STATUS="${HOURLY_SKIP_STATUS:-1}"
HOURLY_MAINT_TIMEOUT_SEC="${HOURLY_MAINT_TIMEOUT_SEC:-90}"
HOURLY_STATUS_TIMEOUT_SEC="${HOURLY_STATUS_TIMEOUT_SEC:-60}"
export HOURLY_PREDICTED_SHARE HOURLY_FRESH_STILL_SHARE HOURLY_RECENT_STILL_DAYS
export HOURLY_SEED_OVER_CHAIN_SHARE HOURLY_FACIAL_LOOKBACK_DAYS HOURLY_FACIAL_DRAIN_EVERY
export HOURLY_I2V_GEX_DRAIN_EVERY
export HOURLY_SKIP_STATUS HOURLY_MAINT_TIMEOUT_SEC HOURLY_STATUS_TIMEOUT_SEC

# Families maintained every tick (deposit / submit / status).
MAINT_FAMILIES=(FB9_GEX2 FB9_GEX2_identity_anchor FB9_GEX_FACIAL FB9_GEX X-KNEEL-FB9 FB9-FaceBlast BounceDanceA FB8VA4 FB8VB2 FB8VA5-ZOOMOUT Breast-shake-FB8VA5)

mkdir -p "$(dirname "$LOG")" "$(dirname "$STATE")"

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

queue_counts() {
  local qf
  qf=$(mktemp)
  curl -sf "$COMFY/queue" > "$qf"
  python3 - "$qf" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    q = json.load(f)
print(len(q.get("queue_running", [])), len(q.get("queue_pending", [])))
PY
  rm -f "$qf"
}

factory_pending_count() {
  cd "$SCRIPTS" && python3 shape_factory_hourly.py pending-count --jobs-dir "$JOBS_DIR"
}

read_state() {
  if [ -f "$STATE" ]; then
    cat "$STATE"
  else
    echo '{"sample_cursor":0,"phase":"idle"}'
  fi
}

shape_for_family() {
  echo "$REPO/.data/shapes/$1.shape.yaml"
}

pools_for_family() {
  echo "$REPO/.data/pools/$1/pools.yaml"
}

load_schedule() {
  cd "$SCRIPTS" && python3 shape_factory_hourly.py schedule-status --schedule "$SCHEDULE"
}

# Args: comfy_waiting [factory_pending]
queue_policy() {
  local fp="${2:-}"
  if [ -z "$fp" ]; then
    fp=$(factory_pending_count)
  fi
  cd "$SCRIPTS" && python3 shape_factory_hourly.py queue-policy \
    --pending "$1" --factory-pending "$fp" \
    --schedule "$SCHEDULE" \
    --queue-min "$HOURLY_QUEUE_MIN" --queue-max "$HOURLY_QUEUE_MAX" \
    --pending-queue-max "$HOURLY_PENDING_MAX" \
    --submit-mode "$HOURLY_SUBMIT_MODE"
}

policy_field() {
  python3 -c "import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2], ''))" "$1" "$2"
}

mark_tick() {
  cd "$SCRIPTS" && python3 shape_factory_hourly.py schedule-set --schedule "$SCHEDULE" --mark-tick >/dev/null
}

maybe_submit() {
  # Args: family destination
  local fam="$1" dest="$2"
  if [ "$dest" = "comfy" ]; then
    python3 shape_factory.py submit --pending-only --family "$fam" >> "$LOG" 2>&1 || true
  else
    log "left pending (destination=$dest) family=$fam"
  fi
}

run_maintenance() {
  # Deposit/status after fill (or when skipping fill). Timeouts keep one hung family
  # from blocking the systemd oneshot for hours.
  local slots="${1:-0}"
  log "maintenance start submit_slots=$slots skip_status=$HOURLY_SKIP_STATUS timeout=${HOURLY_MAINT_TIMEOUT_SEC}s"
  (
    cd "$SCRIPTS"
    for fam in "${MAINT_FAMILIES[@]}"; do
      timeout "${HOURLY_MAINT_TIMEOUT_SEC}" python3 shape_factory.py deposit --quiet --family "$fam" >> "$LOG" 2>&1 || true
      if [ "$slots" -gt 0 ]; then
        timeout "${HOURLY_MAINT_TIMEOUT_SEC}" python3 shape_factory.py submit --pending-only --quiet --family "$fam" --limit "$slots" >> "$LOG" 2>&1 || true
        read -r RUN PEND < <(queue_counts) || true
        slots=$(policy_field "$(queue_policy "${PEND:-0}" 0)" submit_slots)
        slots=${slots:-0}
      fi
      if [ "$HOURLY_SKIP_STATUS" != "1" ]; then
        timeout "${HOURLY_STATUS_TIMEOUT_SEC}" python3 shape_factory.py status --quiet --family "$fam" >> "$LOG" 2>&1 || true
      fi
    done
  )
  log "maintenance done"
}

log "=== hourly tick ==="

SCHEDULE_JSON=$(load_schedule)
SCHEDULE_DUE=$(policy_field "$SCHEDULE_JSON" due)
HOURLY_QUEUE_MIN=$(python3 -c "import json,sys; print(int(json.loads(sys.argv[1])['schedule']['comfy_queue_min']))" "$SCHEDULE_JSON")
HOURLY_QUEUE_MAX=$(python3 -c "import json,sys; print(int(json.loads(sys.argv[1])['schedule']['comfy_queue_max']))" "$SCHEDULE_JSON")
HOURLY_PENDING_MAX=$(python3 -c "import json,sys; print(int(json.loads(sys.argv[1])['schedule']['pending_queue_max']))" "$SCHEDULE_JSON")
HOURLY_SUBMIT_MODE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['schedule']['submit_mode'])" "$SCHEDULE_JSON")
HOURLY_INTERVAL=$(python3 -c "import json,sys; print(int(json.loads(sys.argv[1])['schedule']['interval_minutes']))" "$SCHEDULE_JSON")
HOURLY_ENABLED=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['schedule'].get('enabled', True))" "$SCHEDULE_JSON")
NEXT_DUE=$(policy_field "$SCHEDULE_JSON" next_due_at)
export HOURLY_QUEUE_MIN HOURLY_QUEUE_MAX

if [ "$HOURLY_ENABLED" != "True" ]; then
  log "skip — schedule disabled"
  exit 0
fi
if [ "$SCHEDULE_DUE" != "True" ]; then
  log "skip — not due (interval=${HOURLY_INTERVAL}m next=$NEXT_DUE mode=$HOURLY_SUBMIT_MODE comfy_max=$HOURLY_QUEUE_MAX pending_max=$HOURLY_PENDING_MAX)"
  exit 0
fi

STATE_JSON=$(read_state)
PHASE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('phase','idle'))" "$STATE_JSON")
CURSOR=$(python3 -c "import json,sys; print(int(json.loads(sys.argv[1]).get('sample_cursor',0)))" "$STATE_JSON")

INBOX_JSON=$(cd "$SCRIPTS" && python3 ingest_windows_input_inbox.py --ensure --apply --inbox "${WINDOWS_INPUT_INBOX:-/mnt/e/comfyui-runpod-inbox}" --dest "${COMFYUI_BIND_INPUT_DIR:-/home/yuji/comfyui-runpod-data/input}" 2>>"$LOG" || true)
log "windows-inbox ${INBOX_JSON:-failed}"
STILL_SCAN=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py input-stills-scan --data-root "$REPO/.data" 2>>"$LOG" || true)
log "input-stills-scan ${STILL_SCAN:-failed}"

# Fill first so heavy deposit/status cannot starve image-based seeds for hours.
read -r RUN PEND < <(queue_counts)
FACTORY_PENDING=$(factory_pending_count)
POLICY_JSON=$(queue_policy "$PEND" "$FACTORY_PENDING")
ADVANCE=$(policy_field "$POLICY_JSON" advance)
REASON=$(policy_field "$POLICY_JSON" reason)
DEST=$(policy_field "$POLICY_JSON" destination)
SUBMIT_SLOTS=$(policy_field "$POLICY_JSON" submit_slots)
SUBMIT_SLOTS=${SUBMIT_SLOTS:-0}
log "comfy queue running=$RUN waiting=$PEND factory_pending=$FACTORY_PENDING min=$HOURLY_QUEUE_MIN max=$HOURLY_QUEUE_MAX pending_max=$HOURLY_PENDING_MAX mode=$HOURLY_SUBMIT_MODE advance=$ADVANCE dest=$DEST ($REASON)"

if [ "$ADVANCE" != "True" ]; then
  log "skip hourly fill (phase=$PHASE reason=$REASON)"
  run_maintenance "$SUBMIT_SLOTS"
  mark_tick
  exit 0
fi

if [ "$ADVANCE_CHAIN" != "1" ]; then
  log "ADVANCE_CHAIN=0 — maintenance only"
  run_maintenance "$SUBMIT_SLOTS"
  mark_tick
  exit 0
fi

dev_args=()
if [ "$DEV_CHAIN" = "1" ]; then dev_args+=(--dev); fi
# Distinct path + name stem so timer ticks are easy to find while debugging.
HOURLY_PREFIX_ROOT="${HOURLY_PREFIX_ROOT:-og/%date:yyyy-MM-dd%/hourly}"
HOURLY_JOB_KEY_PREFIX="${HOURLY_JOB_KEY_PREFIX:-hourly}"

# Repeat fills until Comfy waiting >= comfy_queue_min (or we cannot submit to Comfy).
FILLS=0
MAX_FILLS="${HOURLY_MAX_FILLS_PER_TICK:-}"
if [ -z "$MAX_FILLS" ]; then
  MAX_FILLS=$HOURLY_QUEUE_MAX
fi
if [ "$MAX_FILLS" -lt "$HOURLY_QUEUE_MIN" ]; then
  MAX_FILLS=$HOURLY_QUEUE_MIN
fi
log "fill loop start waiting=$PEND min=$HOURLY_QUEUE_MIN max_fills=$MAX_FILLS"

while true; do
  read -r RUN PEND < <(queue_counts) || true
  PEND=${PEND:-0}
  FACTORY_PENDING=$(factory_pending_count)
  POLICY_JSON=$(queue_policy "$PEND" "$FACTORY_PENDING")
  ADVANCE=$(policy_field "$POLICY_JSON" advance)
  REASON=$(policy_field "$POLICY_JSON" reason)
  DEST=$(policy_field "$POLICY_JSON" destination)
  SUBMIT_SLOTS=$(policy_field "$POLICY_JSON" submit_slots)
  SUBMIT_SLOTS=${SUBMIT_SLOTS:-0}

  if [ "$PEND" -ge "$HOURLY_QUEUE_MIN" ]; then
    log "fill loop done — waiting=$PEND >= min=$HOURLY_QUEUE_MIN fills=$FILLS"
    break
  fi
  if [ "$ADVANCE" != "True" ]; then
    log "fill loop stop — advance=false reason=$REASON waiting=$PEND fills=$FILLS"
    break
  fi
  if [ "$DEST" != "comfy" ] && [ "$FILLS" -gt 0 ]; then
    log "fill loop stop — dest=$DEST (cannot raise Comfy waiting; still $PEND < min=$HOURLY_QUEUE_MIN) fills=$FILLS"
    break
  fi
  if [ "$FILLS" -ge "$MAX_FILLS" ]; then
    log "fill loop stop — hit max_fills=$MAX_FILLS waiting=$PEND min=$HOURLY_QUEUE_MIN"
    break
  fi

  STATE_JSON=$(read_state)
  CURSOR=$(python3 -c "import json,sys; print(int(json.loads(sys.argv[1]).get('sample_cursor',0)))" "$STATE_JSON")
  HOURLY_SUFFIX="_$(date -u +%Y%m%d%H%M%S)_f${FILLS}"
  log "fill iter=$((FILLS + 1)) waiting=$PEND min=$HOURLY_QUEUE_MIN dest=$DEST cursor=$CURSOR"

# Phase 1: recent GEX2 complete without FACIAL child (throttled)
NEED_FACIAL_JSON=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py need-facial --data-root "$REPO/.data")
NEED_FACIAL_KEY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('job_key') or '')" "$NEED_FACIAL_JSON")
if [ -n "$NEED_FACIAL_KEY" ]; then
  FACIAL_OK=$(cd "$SCRIPTS" && python3 -c "from shape_factory_hourly import want_facial_chain; import sys; print('1' if want_facial_chain(int(sys.argv[1])) else '0')" "$CURSOR")
  if [ "$FACIAL_OK" != "1" ]; then
    log "phase=skip_facial — not this cursor (HOURLY_FACIAL_DRAIN_EVERY=$HOURLY_FACIAL_DRAIN_EVERY) pending=$NEED_FACIAL_KEY"
    NEED_FACIAL_KEY=""
  else
    SEED_OVER=$(cd "$SCRIPTS" && python3 -c "from shape_factory_hourly import want_seed_over_chain; import sys; print('1' if want_seed_over_chain(int(sys.argv[1])) else '0')" "$CURSOR")
    if [ "$SEED_OVER" = "1" ]; then
      log "phase=seed — skip facial this tick (HOURLY_SEED_OVER_CHAIN_SHARE) pending=$NEED_FACIAL_KEY"
      NEED_FACIAL_KEY=""
    fi
  fi
fi
if [ -n "$NEED_FACIAL_KEY" ]; then
  NEED_FACIAL_VID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('video') or '')" "$NEED_FACIAL_JSON")
  # source_ref = GEX2 parent clip — lineage only (not a FACIAL graph slot).
  NEED_FACIAL_REF=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('source_ref') or '')" "$NEED_FACIAL_JSON")
  log "phase=facial — GEX2 complete without FACIAL ($NEED_FACIAL_KEY)"
  BIND_FACIAL=$(mktemp --suffix=.yaml)
  python3 - "$NEED_FACIAL_VID" "$BIND_FACIAL" <<'PY'
import sys
from pathlib import Path
vid, out = sys.argv[1], Path(sys.argv[2])
def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')
out.write_text(
    "\n".join(['source_video:', '  from: path', f'  path: "{esc(vid)}"']) + "\n",
    encoding="utf-8",
)
PY
  (
    cd "$SCRIPTS"
    python3 shape_factory.py generate \
      --shape "$(shape_for_family FB9_GEX_FACIAL)" \
      --pools "$(pools_for_family FB9_GEX_FACIAL)" \
      --binds-override "$BIND_FACIAL" \
      --pick zip --limit 1 --job-suffix "$HOURLY_SUFFIX" \
      --output-prefix-root "$HOURLY_PREFIX_ROOT" \
      --job-key-prefix "$HOURLY_JOB_KEY_PREFIX" \
      "${dev_args[@]}" >> "$LOG" 2>&1
    maybe_submit FB9_GEX_FACIAL "$DEST"
  )
  rm -f "$BIND_FACIAL"
  python3 - "$STATE_JSON" "$NEED_FACIAL_KEY" "$NEED_FACIAL_VID" "$NEED_FACIAL_REF" "$STATE" "$CURSOR" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(sys.argv[1])
cursor = int(sys.argv[6])
data["phase"] = "facial_queued"
data["last_family"] = "FB9_GEX_FACIAL"
data["last_pick_mode"] = "chain"
data["last_step"] = "chain_facial"
data["last_gex2_job"] = sys.argv[2]
data["last_gex2_video"] = sys.argv[3]
data["last_gex2_source_ref"] = sys.argv[4]
# Advance cursor on chain ticks so HOURLY_SEED_OVER_CHAIN_SHARE re-rolls next hour.
data["sample_cursor"] = cursor + 1
Path(sys.argv[5]).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
  FILLS=$((FILLS + 1))
  log "facial step queued dest=$DEST (next cursor=$((CURSOR + 1))) fills=$FILLS"
  continue
fi

# Phase 2: i2v/still-family complete without FB9_GEX child (FaceBlast, BounceDanceA, Kneel, …)
NEED_I2V_JSON=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py need-gex-from-i2v --data-root "$REPO/.data")
NEED_I2V_KEY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('job_key') or '')" "$NEED_I2V_JSON")
if [ -n "$NEED_I2V_KEY" ]; then
  I2V_OK=$(cd "$SCRIPTS" && python3 -c "from shape_factory_hourly import want_i2v_gex_chain; import sys; print('1' if want_i2v_gex_chain(int(sys.argv[1])) else '0')" "$CURSOR")
  if [ "$I2V_OK" != "1" ]; then
    log "phase=skip_gex_from_i2v — not this cursor (HOURLY_I2V_GEX_DRAIN_EVERY=$HOURLY_I2V_GEX_DRAIN_EVERY) pending=$NEED_I2V_KEY"
    NEED_I2V_KEY=""
  fi
fi
if [ -n "$NEED_I2V_KEY" ]; then
  NEED_I2V_FAM=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('producer_family') or '')" "$NEED_I2V_JSON")
  NEED_I2V_VID=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('video') or '')" "$NEED_I2V_JSON")
  log "phase=gex_from_i2v — $NEED_I2V_FAM complete without GEX ($NEED_I2V_KEY)"
  BIND_I2V=$(mktemp --suffix=.yaml)
  python3 - "$NEED_I2V_VID" "$BIND_I2V" <<'PY'
import sys
from pathlib import Path
vid, out = sys.argv[1], Path(sys.argv[2])
# Escape for YAML double-quoted scalar
esc = vid.replace("\\", "\\\\").replace('"', '\\"')
out.write_text(f'source_video:\n  from: path\n  path: "{esc}"\n', encoding="utf-8")
PY
  (
    cd "$SCRIPTS"
    python3 shape_factory.py generate \
      --shape "$(shape_for_family FB9_GEX)" \
      --pools "$(pools_for_family FB9_GEX)" \
      --binds-override "$BIND_I2V" \
      --pick zip --limit 1 --job-suffix "$HOURLY_SUFFIX" \
      --output-prefix-root "$HOURLY_PREFIX_ROOT" \
      --job-key-prefix "$HOURLY_JOB_KEY_PREFIX" \
      "${dev_args[@]}" >> "$LOG" 2>&1
    maybe_submit FB9_GEX "$DEST"
  )
  rm -f "$BIND_I2V"
  python3 - "$STATE_JSON" "$NEED_I2V_KEY" "$NEED_I2V_FAM" "$NEED_I2V_VID" "$STATE" "$CURSOR" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(sys.argv[1])
cursor = int(sys.argv[6])
data["phase"] = "gex_from_i2v_queued"
data["last_family"] = "FB9_GEX"
data["last_pick_mode"] = "chain"
data["last_step"] = "chain_gex_from_i2v"
data["last_i2v_job"] = sys.argv[2]
data["last_i2v_producer"] = sys.argv[3]
data["last_i2v_video"] = sys.argv[4]
data["sample_cursor"] = cursor + 1
Path(sys.argv[5]).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
  FILLS=$((FILLS + 1))
  log "gex-from-i2v step queued producer=$NEED_I2V_FAM dest=$DEST (next cursor=$((CURSOR + 1))) fills=$FILLS"
  continue
fi

# Phase 3: weighted seed family (replay / derive / pool_product)
FAM_JSON=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py select-family --cursor "$CURSOR")
FAMILY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['family'])" "$FAM_JSON")
PLAN_JSON=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py plan-step --state "$STATE" --data-root "$REPO/.data" --family "$FAMILY")
PLAN_OK=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('ok'))" "$PLAN_JSON")
if [ "$PLAN_OK" != "True" ]; then
  PLAN_ERR=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('error',''))" "$PLAN_JSON")
  log "seed skipped family=$FAMILY (${PLAN_ERR:-no plan}) — stopping fill loop fills=$FILLS"
  break
fi

# Prefer identity-anchor plate when the plan upgraded Extend (family may change).
PLAN_FAMILY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('family') or '')" "$PLAN_JSON")
if [ -n "$PLAN_FAMILY" ]; then
  FAMILY="$PLAN_FAMILY"
fi
UPGRADED_FROM=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('upgraded_from') or '')" "$PLAN_JSON")
IDENTITY_EV=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('identity_evidence') or '')" "$PLAN_JSON")

PICK_MODE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('pick_mode','replay'))" "$PLAN_JSON")
RATING_KIND=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('rating_kind',''))" "$PLAN_JSON")
STEP_KIND=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('step',''))" "$PLAN_JSON")
DISP_ENTRY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('disposition_entry',''))" "$PLAN_JSON")
COMBO_KEY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('combo_key',''))" "$PLAN_JSON")
RECIPE_COUNT=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('recipe_count','?'))" "$PLAN_JSON")
REPLAY_SOURCE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('source',''))" "$PLAN_JSON")
PLAN_FILE=$(mktemp)
python3 -c "import json,sys; json.dump(json.loads(sys.argv[1]), open(sys.argv[2],'w',encoding='utf-8'), ensure_ascii=False, indent=2)" "$PLAN_JSON" "$PLAN_FILE"

# Predicted/inferred seeds always generate as derive (even if plan said otherwise).
if [ "$RATING_KIND" = "predicted" ] || [ "$STEP_KIND" = "predicted_derive" ]; then
  PICK_MODE=derive
  # Ensure plan carries derived disposition for deposit stamping.
  python3 - "$PLAN_FILE" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
data = json.loads(p.read_text(encoding="utf-8"))
data["pick_mode"] = "derive"
data["disposition_entry"] = data.get("disposition_entry") or "derived"
data["rating_kind"] = data.get("rating_kind") or "predicted"
if not data.get("disposition_note"):
    data["disposition_note"] = "hourly predicted/inferred seed — rate to validate prediction"
p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(data["disposition_entry"])
PY
  DISP_ENTRY=$(python3 -c "import json; print(json.load(open('$PLAN_FILE')).get('disposition_entry',''))")
fi

if [ -n "$UPGRADED_FROM" ]; then
  log "phase=seed — family=$FAMILY (from $UPGRADED_FROM identity=$IDENTITY_EV) cursor=$CURSOR pick_mode=$PICK_MODE step=$STEP_KIND rating_kind=${RATING_KIND:-?} disposition=${DISP_ENTRY:-?} recipes=$RECIPE_COUNT source=$REPLAY_SOURCE combo=$COMBO_KEY"
else
  log "phase=seed — family=$FAMILY cursor=$CURSOR pick_mode=$PICK_MODE step=$STEP_KIND rating_kind=${RATING_KIND:-?} disposition=${DISP_ENTRY:-?} recipes=$RECIPE_COUNT source=$REPLAY_SOURCE combo=$COMBO_KEY"
fi
GEN_RC=0
(
  cd "$SCRIPTS"
  python3 shape_factory_creation_control.py create-generate \
    --shape "$(shape_for_family "$FAMILY")" \
    --pools "$(pools_for_family "$FAMILY")" \
    --data-root "$REPO/.data" \
    --workflow-dir "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/shape_factory" \
    --job-dir "$REPO/.data/shape_factory/jobs" \
    --pick "$PICK_MODE" --limit 1 --picks-json "$PLAN_FILE" --job-suffix "$HOURLY_SUFFIX" \
    --output-prefix-root "$HOURLY_PREFIX_ROOT" \
    --job-key-prefix "$HOURLY_JOB_KEY_PREFIX" \
    "${dev_args[@]}" >> "$LOG" 2>&1
) || GEN_RC=$?
(
  cd "$SCRIPTS"
  maybe_submit "$FAMILY" "$DEST"
)
rm -f "$PLAN_FILE"

NEXT_CURSOR=$((CURSOR + 1))
python3 - "$STATE_JSON" "$STATE" "$FAMILY" "$PICK_MODE" "$COMBO_KEY" "$REPLAY_SOURCE" "$GEN_RC" "$NEXT_CURSOR" "$RATING_KIND" "$STEP_KIND" "$DISP_ENTRY" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(sys.argv[1])
data["phase"] = "seed_queued"
data["last_family"] = sys.argv[3]
data["sample_cursor"] = int(sys.argv[8])
data["last_pick_mode"] = sys.argv[4]
data["last_combo_key"] = sys.argv[5]
data["last_replay_source"] = sys.argv[6]
data["last_generate_rc"] = int(sys.argv[7])
data["last_rating_kind"] = sys.argv[9]
data["last_step"] = sys.argv[10]
data["last_disposition_entry"] = sys.argv[11] if len(sys.argv) > 11 else ""
Path(sys.argv[2]).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
FILLS=$((FILLS + 1))
if [ "$GEN_RC" != "0" ]; then
  log "seed generate failed family=$FAMILY rc=$GEN_RC — advanced cursor to $NEXT_CURSOR anyway fills=$FILLS"
else
  log "seed queued family=$FAMILY pick_mode=$PICK_MODE rating_kind=${RATING_KIND:-?} dest=$DEST (next cursor=$NEXT_CURSOR) fills=$FILLS"
fi
# If first fill spilled to factory pending (comfy already at max), don't spin.
if [ "$DEST" != "comfy" ]; then
  log "fill loop stop after pending spill fills=$FILLS"
  break
fi
done

mark_tick
log "fill loop finished fills=$FILLS"
run_maintenance "$SUBMIT_SLOTS"
exit 0
