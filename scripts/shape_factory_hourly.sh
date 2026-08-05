#!/usr/bin/env bash
# Hourly shape-factory maintenance + optional fill when Comfy is idle *and*
# there are no factory jobs still awaiting submit.
# Pending drain (shape_factory_pending_drain) owns pushing pending onto Comfy;
# this tick only generates new hourly work as a fill when that set is empty.
# Priority when filling: GEX2→FACIAL chain, Kneel→GEX2 chain, then weighted seed.
# Install timer: bash scripts/install-shape-factory-hourly.sh
set -euo pipefail

REPO="${REPO:-/home/yuji/src/comfyui-runpod}"
SCRIPTS="$REPO/workspace/scripts"
LOG="${LOG:-$REPO/.data/shape_factory/hourly.log}"
STATE="${STATE:-$REPO/.data/shape_factory/hourly-state.json}"
JOBS_DIR="${JOBS_DIR:-$REPO/.data/shape_factory/jobs}"
CHAIN_MANIFEST="${CHAIN_MANIFEST:-$REPO/.data/chains/best-examples.chain.yaml}"
BINDS_FACIAL="$REPO/.data/pipelines/binds/facial-from-latest-gex2.yaml"
BINDS_KNEEL_GEX2="$REPO/.data/pipelines/binds/gex2-from-latest-kneel.yaml"
COMFY="${COMFY:-http://127.0.0.1:8188}"
ADVANCE_CHAIN="${ADVANCE_CHAIN:-1}"
DEV_CHAIN="${DEV_CHAIN:-0}"
HOURLY_QUEUE_MIN="${HOURLY_QUEUE_MIN:-1}"
HOURLY_QUEUE_MAX="${HOURLY_QUEUE_MAX:-2}"
HOURLY_PREDICTED_SHARE="${HOURLY_PREDICTED_SHARE:-0.35}"
export HOURLY_QUEUE_MIN HOURLY_QUEUE_MAX HOURLY_PREDICTED_SHARE

# Families maintained every tick (deposit / submit / status).
MAINT_FAMILIES=(FB9_GEX2 FB9_GEX_FACIAL FB9_GEX X-KNEEL-FB9 FB9-FaceBlast)

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

# Args: comfy_waiting [factory_pending]
queue_policy() {
  local fp="${2:-}"
  if [ -z "$fp" ]; then
    fp=$(factory_pending_count)
  fi
  cd "$SCRIPTS" && python3 shape_factory_hourly.py queue-policy \
    --pending "$1" --factory-pending "$fp" \
    --queue-min "$HOURLY_QUEUE_MIN" --queue-max "$HOURLY_QUEUE_MAX"
}

policy_field() {
  python3 -c "import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2], ''))" "$1" "$2"
}

log "=== hourly tick ==="

STATE_JSON=$(read_state)
PHASE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('phase','idle'))" "$STATE_JSON")
CURSOR=$(python3 -c "import json,sys; print(int(json.loads(sys.argv[1]).get('sample_cursor',0)))" "$STATE_JSON")

read -r RUN PEND < <(queue_counts)
# Maintenance may still push existing pending; factory_pending=0 here so
# submit_slots only reflect Comfy waiting room (drain owns priority).
SUBMIT_SLOTS=$(policy_field "$(queue_policy "$PEND" 0)" submit_slots)
SUBMIT_SLOTS=${SUBMIT_SLOTS:-0}

(
  cd "$SCRIPTS"
  for fam in "${MAINT_FAMILIES[@]}"; do
    python3 shape_factory.py deposit --quiet --family "$fam" >> "$LOG" 2>&1 || true
    if [ "$SUBMIT_SLOTS" -gt 0 ]; then
      python3 shape_factory.py submit --pending-only --quiet --family "$fam" --limit "$SUBMIT_SLOTS" >> "$LOG" 2>&1 || true
      read -r RUN PEND < <(queue_counts)
      SUBMIT_SLOTS=$(policy_field "$(queue_policy "$PEND" 0)" submit_slots)
      SUBMIT_SLOTS=${SUBMIT_SLOTS:-0}
    fi
    python3 shape_factory.py status --quiet --family "$fam" >> "$LOG" 2>&1 || true
  done
)

read -r RUN PEND < <(queue_counts)
FACTORY_PENDING=$(factory_pending_count)
POLICY_JSON=$(queue_policy "$PEND" "$FACTORY_PENDING")
ADVANCE=$(policy_field "$POLICY_JSON" advance)
REASON=$(policy_field "$POLICY_JSON" reason)
log "comfy queue running=$RUN waiting=$PEND factory_pending=$FACTORY_PENDING min=$HOURLY_QUEUE_MIN max=$HOURLY_QUEUE_MAX advance=$ADVANCE ($REASON)"

if [ "$ADVANCE" != "True" ]; then
  log "skip hourly fill (phase=$PHASE reason=$REASON)"
  exit 0
fi

if [ "$ADVANCE_CHAIN" != "1" ]; then
  log "ADVANCE_CHAIN=0 — maintenance only"
  exit 0
fi

dev_args=()
if [ "$DEV_CHAIN" = "1" ]; then dev_args+=(--dev); fi
# Distinct path + name stem so timer ticks are easy to find while debugging.
HOURLY_PREFIX_ROOT="${HOURLY_PREFIX_ROOT:-og/%date:yyyy-MM-dd%/hourly}"
HOURLY_JOB_KEY_PREFIX="${HOURLY_JOB_KEY_PREFIX:-hourly}"
HOURLY_SUFFIX="_$(date -u +%Y%m%d%H%M)"

# Phase 1: GEX2 complete without FACIAL child
NEED_FACIAL=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py need-facial --data-root "$REPO/.data")
if [ -n "$NEED_FACIAL" ]; then
  log "phase=facial — GEX2 complete without FACIAL ($NEED_FACIAL)"
  (
    cd "$SCRIPTS"
    python3 shape_factory.py generate \
      --shape "$(shape_for_family FB9_GEX_FACIAL)" \
      --pools "$(pools_for_family FB9_GEX_FACIAL)" \
      --binds-override "$BINDS_FACIAL" \
      --pick zip --limit 1 --job-suffix "$HOURLY_SUFFIX" \
      --output-prefix-root "$HOURLY_PREFIX_ROOT" \
      --job-key-prefix "$HOURLY_JOB_KEY_PREFIX" \
      "${dev_args[@]}" >> "$LOG" 2>&1
    python3 shape_factory.py submit --pending-only --family FB9_GEX_FACIAL >> "$LOG" 2>&1
  )
  python3 - "$STATE_JSON" "$NEED_FACIAL" "$STATE" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(sys.argv[1])
data["phase"] = "facial_queued"
data["last_family"] = "FB9_GEX_FACIAL"
data["last_pick_mode"] = "chain"
data["last_gex2_job"] = sys.argv[2]
Path(sys.argv[3]).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
  log "facial step queued"
  exit 0
fi

# Phase 2: Kneel complete without GEX2 child
NEED_KNEEL_GEX2=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py need-gex2-from-kneel --data-root "$REPO/.data")
if [ -n "$NEED_KNEEL_GEX2" ]; then
  log "phase=gex2_from_kneel — Kneel complete without GEX2 ($NEED_KNEEL_GEX2)"
  (
    cd "$SCRIPTS"
    python3 shape_factory.py generate \
      --shape "$(shape_for_family FB9_GEX2)" \
      --pools "$(pools_for_family FB9_GEX2)" \
      --binds-override "$BINDS_KNEEL_GEX2" \
      --pick zip --limit 1 --job-suffix "$HOURLY_SUFFIX" \
      --output-prefix-root "$HOURLY_PREFIX_ROOT" \
      --job-key-prefix "$HOURLY_JOB_KEY_PREFIX" \
      "${dev_args[@]}" >> "$LOG" 2>&1
    python3 shape_factory.py submit --pending-only --family FB9_GEX2 >> "$LOG" 2>&1
  )
  python3 - "$STATE_JSON" "$NEED_KNEEL_GEX2" "$STATE" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(sys.argv[1])
data["phase"] = "gex2_from_kneel_queued"
data["last_family"] = "FB9_GEX2"
data["last_pick_mode"] = "chain"
data["last_kneel_job"] = sys.argv[2]
Path(sys.argv[3]).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
  log "gex2-from-kneel step queued"
  exit 0
fi

# Phase 3: weighted seed family (replay / derive / pool_product)
FAM_JSON=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py select-family --cursor "$CURSOR")
FAMILY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['family'])" "$FAM_JSON")
PLAN_JSON=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py plan-step --state "$STATE" --data-root "$REPO/.data" --family "$FAMILY")
PLAN_OK=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('ok'))" "$PLAN_JSON")
if [ "$PLAN_OK" != "True" ]; then
  PLAN_ERR=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('error',''))" "$PLAN_JSON")
  log "seed skipped family=$FAMILY (${PLAN_ERR:-no plan})"
  exit 0
fi

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

log "phase=seed — family=$FAMILY cursor=$CURSOR pick_mode=$PICK_MODE step=$STEP_KIND rating_kind=${RATING_KIND:-?} disposition=${DISP_ENTRY:-?} recipes=$RECIPE_COUNT source=$REPLAY_SOURCE combo=$COMBO_KEY"
GEN_RC=0
(
  cd "$SCRIPTS"
  python3 shape_factory.py generate \
    --shape "$(shape_for_family "$FAMILY")" \
    --pools "$(pools_for_family "$FAMILY")" \
    --pick "$PICK_MODE" --limit 1 --picks-json "$PLAN_FILE" --job-suffix "$HOURLY_SUFFIX" \
    --output-prefix-root "$HOURLY_PREFIX_ROOT" \
    --job-key-prefix "$HOURLY_JOB_KEY_PREFIX" \
    "${dev_args[@]}" >> "$LOG" 2>&1
) || GEN_RC=$?
(
  cd "$SCRIPTS"
  python3 shape_factory.py submit --pending-only --family "$FAMILY" >> "$LOG" 2>&1 || true
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
if [ "$GEN_RC" != "0" ]; then
  log "seed generate failed family=$FAMILY rc=$GEN_RC — advanced cursor to $NEXT_CURSOR anyway"
else
  log "seed queued family=$FAMILY pick_mode=$PICK_MODE rating_kind=${RATING_KIND:-?} (next cursor=$NEXT_CURSOR)"
fi
