#!/usr/bin/env bash
# Hourly shape-factory maintenance + optional chain advance when Comfy queue is idle.
# Install timer: bash scripts/install-shape-factory-hourly.sh
set -euo pipefail

REPO="${REPO:-/home/yuji/src/comfyui-runpod}"
SCRIPTS="$REPO/workspace/scripts"
LOG="${LOG:-$REPO/.data/shape_factory/hourly.log}"
STATE="${STATE:-$REPO/.data/shape_factory/hourly-state.json}"
CHAIN_MANIFEST="${CHAIN_MANIFEST:-$REPO/.data/chains/best-examples.chain.yaml}"
BINDS="$REPO/.data/pipelines/binds/facial-from-latest-gex2.yaml"
COMFY="${COMFY:-http://127.0.0.1:8188}"
ADVANCE_CHAIN="${ADVANCE_CHAIN:-1}"
DEV_CHAIN="${DEV_CHAIN:-0}"

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

read_state() {
  if [ -f "$STATE" ]; then
    cat "$STATE"
  else
    echo '{"sample_cursor":0,"phase":"idle"}'
  fi
}

write_state() {
  python3 - "$STATE" <<PY
import json, sys
from pathlib import Path
data = json.loads(sys.argv[1])
Path("$STATE").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
}

log "=== hourly tick ==="

(
  cd "$SCRIPTS"
  python3 shape_factory.py deposit --family FB9_GEX2 >> "$LOG" 2>&1 || true
  python3 shape_factory.py deposit --family FB9_GEX_FACIAL >> "$LOG" 2>&1 || true
  python3 shape_factory.py submit --pending-only --family FB9_GEX2 >> "$LOG" 2>&1 || true
  python3 shape_factory.py submit --pending-only --family FB9_GEX_FACIAL >> "$LOG" 2>&1 || true
  python3 shape_factory.py status --family FB9_GEX2 >> "$LOG" 2>&1 || true
  python3 shape_factory.py status --family FB9_GEX_FACIAL >> "$LOG" 2>&1 || true
)

read -r RUN PEND < <(queue_counts)
log "comfy queue running=$RUN pending=$PEND"

STATE_JSON=$(read_state)
PHASE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('phase','idle'))" "$STATE_JSON")
CURSOR=$(python3 -c "import json,sys; print(int(json.loads(sys.argv[1]).get('sample_cursor',0)))" "$STATE_JSON")

if [ "$RUN" != "0" ] || [ "$PEND" != "0" ]; then
  log "queue busy — skip chain advance (phase=$PHASE)"
  exit 0
fi

if [ "$ADVANCE_CHAIN" != "1" ]; then
  log "ADVANCE_CHAIN=0 — maintenance only"
  exit 0
fi

dev_args=()
if [ "$DEV_CHAIN" = "1" ]; then dev_args+=(--dev); fi
HOURLY_SUFFIX="_h$(date -u +%Y%m%d%H)"

# Phase: need FACIAL for latest complete GEX2 without a submitted FACIAL using its deposit?
NEED_FACIAL=$(python3 <<'PY'
import json
from pathlib import Path
job_dir = Path("/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs")
gex2_done = []
for p in sorted((job_dir / "FB9_GEX2").glob("*.job.json")):
    j = json.loads(p.read_text())
    if j.get("status") != "complete":
        continue
    dep = j.get("deposit") or {}
    vids = dep.get("videos") or []
    if not vids:
        continue
    gex2_done.append((str(j.get("job_key")), vids[-1]))
facial_sources = set()
for p in (job_dir / "FB9_GEX_FACIAL").glob("*.job.json"):
    j = json.loads(p.read_text())
    sv = (j.get("bindings") or {}).get("source_video") or {}
    facial_sources.add(str(sv.get("path") or ""))
for job_key, vid in reversed(gex2_done):
    if vid not in facial_sources:
        print(job_key)
        break
PY
)

if [ -n "$NEED_FACIAL" ]; then
  log "phase=facial — GEX2 complete without FACIAL ($NEED_FACIAL)"
  (
    cd "$SCRIPTS"
    python3 shape_factory.py generate \
      --shape ../../.data/shapes/FB9_GEX_FACIAL.shape.yaml \
      --pools ../../.data/pools/FB9_GEX_FACIAL/pools.yaml \
      --binds-override "$BINDS" \
      --pick zip --limit 1 --job-suffix "$HOURLY_SUFFIX" \
      "${dev_args[@]}" >> "$LOG" 2>&1
    python3 shape_factory.py submit --pending-only --family FB9_GEX_FACIAL >> "$LOG" 2>&1
  )
  python3 - "$STATE_JSON" "$NEED_FACIAL" <<'PY'
import json, sys
data = json.loads(sys.argv[1])
data["phase"] = "facial_queued"
data["last_gex2_job"] = sys.argv[2]
from pathlib import Path
Path("/home/yuji/src/comfyui-runpod/.data/shape_factory/hourly-state.json").write_text(
    json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
  log "facial step queued"
  exit 0
fi

# Phase: start next GEX2 sample — replay ("do more OF") or derive ("do more WITH") per appetite
PLAN_JSON=$(cd "$SCRIPTS" && python3 shape_factory_hourly.py plan-step --state "$STATE" --data-root "$REPO/.data" --family FB9_GEX2)
PLAN_OK=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('ok'))" "$PLAN_JSON")
if [ "$PLAN_OK" != "True" ]; then
  PLAN_ERR=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('error',''))" "$PLAN_JSON")
  log "GEX2 replay skipped (${PLAN_ERR:-no plan})"
  exit 0
fi

PICK_MODE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('pick_mode','replay'))" "$PLAN_JSON")
COMBO_KEY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('combo_key',''))" "$PLAN_JSON")
RECIPE_COUNT=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('recipe_count','?'))" "$PLAN_JSON")
REPLAY_SOURCE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('source',''))" "$PLAN_JSON")
PLAN_FILE=$(mktemp)
python3 -c "import json,sys; json.dump(json.loads(sys.argv[1]), open(sys.argv[2],'w',encoding='utf-8'), ensure_ascii=False, indent=2)" "$PLAN_JSON" "$PLAN_FILE"

log "phase=gex2 — cursor=$CURSOR pick_mode=$PICK_MODE recipes=$RECIPE_COUNT source=$REPLAY_SOURCE combo=$COMBO_KEY"
GEN_RC=0
(
  cd "$SCRIPTS"
  python3 shape_factory.py generate \
    --shape ../../.data/shapes/FB9_GEX2.shape.yaml \
    --pools ../../.data/pools/FB9_GEX2/pools.yaml \
    --pick "$PICK_MODE" --limit 1 --picks-json "$PLAN_FILE" --job-suffix "$HOURLY_SUFFIX" \
    "${dev_args[@]}" >> "$LOG" 2>&1
) || GEN_RC=$?
(
  cd "$SCRIPTS"
  python3 shape_factory.py submit --pending-only --family FB9_GEX2 >> "$LOG" 2>&1 || true
)
rm -f "$PLAN_FILE"

NEXT_CURSOR=$((CURSOR + 1))
python3 <<PY
import json
from pathlib import Path
data = json.loads('''$STATE_JSON''')
data["phase"] = "gex2_queued"
data["sample_cursor"] = $NEXT_CURSOR
data["last_pick_mode"] = "$PICK_MODE"
data["last_combo_key"] = "$COMBO_KEY"
data["last_replay_source"] = "$REPLAY_SOURCE"
data["last_generate_rc"] = $GEN_RC
Path("$STATE").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
if [ "$GEN_RC" != "0" ]; then
  log "gex2 generate failed rc=$GEN_RC — advanced cursor to $NEXT_CURSOR anyway"
else
  log "gex2 replay queued (next cursor=$NEXT_CURSOR)"
fi
