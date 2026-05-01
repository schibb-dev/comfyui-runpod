#!/usr/bin/env bash
# Compare Windows vs Linux workspace trees (excludes node_modules, dist, __pycache__,
# nested workspace/**/.git/).
#
# Env overrides:
#   WIN_REPO / LIN_WORKSPACE_ROOT — or set COMFYUI_EXT4_DATA_ROOT for ~/data/comfyui-runpod layout.
set -eu
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

pick_win_repo() {
  if [[ -n "${WIN_REPO:-}" ]]; then echo "$WIN_REPO"; return; fi
  local u
  u="$(whoami)"
  for candidate in "/mnt/c/Users/${u}/Code/comfyui-runpod" "/mnt/c/Users/yuji/Code/comfyui-runpod"; do
    if [[ -d "$candidate" ]]; then echo "$candidate"; return; fi
  done
  echo "/mnt/c/Users/${u}/Code/comfyui-runpod"
}

WIN_BASE="$(pick_win_repo)"
WIN_ROOT="${WIN_WORKSPACE_ROOT:-$WIN_BASE/workspace}"

LIN_ROOT="${LIN_WORKSPACE_ROOT:-}"
if [[ -z "$LIN_ROOT" && -n "${COMFYUI_EXT4_DATA_ROOT:-}" ]]; then
  LIN_ROOT="$COMFYUI_EXT4_DATA_ROOT"
fi
if [[ -z "$LIN_ROOT" && -f .env ]]; then
  bind_in="$(grep '^COMFYUI_BIND_INPUT_DIR=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
  bind_in="${bind_in//\"/}"
  if [[ -n "$bind_in" ]]; then
    LIN_ROOT="$(dirname "$bind_in")"
  fi
fi
LIN_ROOT="${LIN_ROOT:-$REPO/workspace}"

echo "=== Top-level directories (file count & size, excluding node_modules/dist/__pycache__/.git) ==="
printf "%-28s %10s %10s %12s %12s\n" "DIR" "WIN_files" "LIN_files" "WIN_size" "LIN_size"
for name in $(ls -1 "$WIN_ROOT" 2>/dev/null | sort -u); do
  [ ! -d "$WIN_ROOT/$name" ] && continue
  wf=$(find "$WIN_ROOT/$name" \( -path "*/node_modules/*" -o -path "*/dist/*" -o -path "*/__pycache__/*" -o -path "*/.git/*" \) -prune -o -type f -print 2>/dev/null | wc -l)
  lf=$(find "$LIN_ROOT/$name" \( -path "*/node_modules/*" -o -path "*/dist/*" -o -path "*/__pycache__/*" -o -path "*/.git/*" \) -prune -o -type f -print 2>/dev/null | wc -l)
  ws=$(du -sh "$WIN_ROOT/$name" 2>/dev/null | awk '{print $1}')
  ls=$(du -sh "$LIN_ROOT/$name" 2>/dev/null | awk '{print $1}')
  printf "%-28s %10s %10s %12s %12s\n" "$name" "$wf" "$lf" "$ws" "$ls"
done

echo ""
echo "=== Totals (same exclusions) ==="
echo -n "Windows files: "
find "$WIN_ROOT" \( -path "*/node_modules/*" -o -path "*/dist/*" -o -path "*/__pycache__/*" -o -path "*/.git/*" \) -prune -o -type f -print 2>/dev/null | wc -l
echo -n "Linux files:   "
find "$LIN_ROOT" \( -path "*/node_modules/*" -o -path "*/dist/*" -o -path "*/__pycache__/*" -o -path "*/.git/*" \) -prune -o -type f -print 2>/dev/null | wc -l
du -sh "$WIN_ROOT" 2>/dev/null
du -sh "$LIN_ROOT" 2>/dev/null

tmp=/tmp/ws-cmp-$$
find "$WIN_ROOT" \( -path "*/node_modules/*" -o -path "*/dist/*" -o -path "*/__pycache__/*" -o -path "*/.git/*" \) -prune -o -type f -print 2>/dev/null |
  sed "s|^${WIN_ROOT}/||" | sort -u >"${tmp}.win"
find "$LIN_ROOT" \( -path "*/node_modules/*" -o -path "*/dist/*" -o -path "*/__pycache__/*" -o -path "*/.git/*" \) -prune -o -type f -print 2>/dev/null |
  sed "s|^${LIN_ROOT}/||" | sort -u >"${tmp}.lin"

only_win=$(comm -23 "${tmp}.win" "${tmp}.lin" | wc -l)
only_lin=$(comm -13 "${tmp}.win" "${tmp}.lin" | wc -l)
both=$(comm -12 "${tmp}.win" "${tmp}.lin" | wc -l)

echo ""
echo "=== Relative path sets (after exclusions) ==="
echo "Both sides:        $both"
echo "Only Windows:      $only_win"
echo "Only Linux:        $only_lin"

echo ""
echo "=== Sample: 30 paths only on Windows ==="
comm -23 "${tmp}.win" "${tmp}.lin" | head -30

echo ""
echo "=== Sample: 30 paths only on Linux ==="
comm -13 "${tmp}.win" "${tmp}.lin" | head -30

rm -f "${tmp}.win" "${tmp}.lin"
