#!/usr/bin/env bash
# Reclaim root-owned files under the RW shape_factory bind so uid 1000 (ubuntu)
# can write them. Comfy + Experiments UI run as that uid; `docker exec` without
# `-u ubuntu` creates root-owned JSON and the next ubuntu write 500s (EACCES).
#
# Silent when there is nothing to do. On a change: compact summary on stdout,
# full path list in $ROOT/ownership-reclaim.log.
#
# Run as root inside comfyui0-runpod (entrypoint, hourly tick, or
# `docker exec -u 0 comfyui0-runpod /workspace/scripts/reclaim_runtime_ownership.sh`).
set -euo pipefail

[[ "$(id -u)" -eq 0 ]] || exit 0

UIDN="${COMFYUI_RUN_UID:-1000}"
GIDN="${COMFYUI_RUN_GID:-1000}"
ROOT="${1:-${WORKSPACE_PATH:-/workspace}/.data/shape_factory}"
STDOUT_LIST_MAX="${RECLAIM_OWNERSHIP_STDOUT_LIST_MAX:-12}"

[[ -d "$ROOT" ]] || exit 0

changed=()
while IFS= read -r -d '' p; do
  meta="$(stat -c '%U:%G %a %F' "$p" 2>/dev/null || echo "? ?")"
  chown "${UIDN}:${GIDN}" "$p" 2>/dev/null || continue
  extra=""
  if [[ -f "$p" ]]; then
    chmod u+w "$p" 2>/dev/null && extra=" +u+w" || true
  fi
  rel="${p#"$ROOT"/}"
  [[ "$rel" == "$p" ]] && rel="."
  changed+=("${rel}"$'\t'"${meta}${extra}")
done < <(find "$ROOT" -xdev -user 0 -print0 2>/dev/null || true)

n=${#changed[@]}
[[ "$n" -gt 0 ]] || exit 0

stamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
log_file="${ROOT}/ownership-reclaim.log"

{
  echo "${stamp} reclaimed ${n} path(s) under ${ROOT} → ${UIDN}:${GIDN}"
  for row in "${changed[@]}"; do
    rel="${row%%$'\t'*}"
    meta="${row#*$'\t'}"
    echo "  ${rel}  (${meta})"
  done
  echo
} >>"$log_file"

# Compact stdout: counts by first path component, plus a short file list.
declare -A buckets=()
for row in "${changed[@]}"; do
  rel="${row%%$'\t'*}"
  if [[ "$rel" == */* ]]; then
    top="${rel%%/*}"
    buckets["$top"]=$((${buckets[$top]:-0} + 1))
  fi
done

echo "reclaim_runtime_ownership: ${n} root-owned path(s) → ${UIDN}:${GIDN} under ${ROOT}"
if [[ ${#buckets[@]} -gt 0 ]]; then
  for top in $(printf '%s\n' "${!buckets[@]}" | sort); do
    echo "  ${top}/  ${buckets[$top]}"
  done
fi
listed=0
for row in "${changed[@]}"; do
  rel="${row%%$'\t'*}"
  meta="${row#*$'\t'}"
  [[ "$listed" -lt "$STDOUT_LIST_MAX" ]] || break
  echo "  ${rel}  (${meta})"
  listed=$((listed + 1))
done
if [[ "$n" -gt "$listed" ]]; then
  echo "  … ${n} total; full list: ${log_file}"
fi
exit 0
