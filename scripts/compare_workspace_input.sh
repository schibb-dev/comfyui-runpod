#!/usr/bin/env bash
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
WIN_INPUT="${WIN_INPUT_DIR:-$WIN_BASE/workspace/input}"

LIN_INPUT="${LIN_INPUT_DIR:-}"
if [[ -z "$LIN_INPUT" && -f .env ]]; then
  LIN_INPUT="$(grep '^COMFYUI_BIND_INPUT_DIR=' .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r' || true)"
  LIN_INPUT="${LIN_INPUT//\"/}"
fi
LIN_INPUT="${LIN_INPUT:-$REPO/workspace/input}"

WIN="$WIN_INPUT"
LIN="$LIN_INPUT"

echo "=== File counts & sizes ==="
echo -n "Windows ($WIN): "
find "$WIN" -type f 2>/dev/null | wc -l
du -sh "$WIN" 2>/dev/null || true
echo -n "Linux   ($LIN): "
find "$LIN" -type f 2>/dev/null | wc -l
du -sh "$LIN" 2>/dev/null || true

tmp=/tmp/input-compare-$$
find "$WIN" -type f 2>/dev/null | sed "s|^${WIN}/||" | sort -u >"${tmp}.win"
find "$LIN" -type f 2>/dev/null | sed "s|^${LIN}/||" | sort -u >"${tmp}.lin"

only_win=$(comm -23 "${tmp}.win" "${tmp}.lin" | wc -l)
only_lin=$(comm -13 "${tmp}.win" "${tmp}.lin" | wc -l)
both=$(comm -12 "${tmp}.win" "${tmp}.lin" | wc -l)

echo ""
echo "=== Path-set comparison (relative paths) ==="
echo "In both trees:     $both"
echo "Only on Windows:   $only_win"
echo "Only on Linux:     $only_lin"

echo ""
echo "=== Sample: first 25 only-on-Windows ==="
comm -23 "${tmp}.win" "${tmp}.lin" | head -25

echo ""
echo "=== Sample: first 25 only-on-Linux ==="
comm -13 "${tmp}.win" "${tmp}.lin" | head -25

echo ""
echo "=== Same path, different byte size (first 20) ==="
while IFS= read -r rel; do
  [ -z "$rel" ] && continue
  sw="$WIN/$rel"
  sl="$LIN/$rel"
  if [ -f "$sw" ] && [ -f "$sl" ]; then
    cw=$(stat -c%s "$sw" 2>/dev/null || echo 0)
    cl=$(stat -c%s "$sl" 2>/dev/null || echo 0)
    if [ "$cw" != "$cl" ]; then
      echo "$cw $cl $rel"
    fi
  fi
done < <(comm -12 "${tmp}.win" "${tmp}.lin") | head -20

rm -f "${tmp}.win" "${tmp}.lin"
