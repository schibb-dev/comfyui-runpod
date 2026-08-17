#!/usr/bin/env bash
# Log WSL swap/PSI + Comfy RSS so we can see when paging starts.
set -euo pipefail
LOG="${1:-/home/yuji/src/comfyui-runpod/.data/shape_factory/swap_watch.log}"
mkdir -p "$(dirname "$LOG")"
read_vm() { awk -v k="$1" '$1==k {print $2; exit}' /proc/vmstat; }
prev_si=$(read_vm pswpin)
prev_so=$(read_vm pswpout)
echo "$(date -Is) START prev_si=$prev_si prev_so=$prev_so" | tee -a "$LOG"
while true; do
  si=$(read_vm pswpin)
  so=$(read_vm pswpout)
  dsi=$((si - prev_si))
  dso=$((so - prev_so))
  prev_si=$si
  prev_so=$so
  mem=$(awk '/MemAvailable:/ {a=$2} /MemTotal:/ {t=$2} /SwapTotal:/ {st=$2} /SwapFree:/ {sf=$2} END {printf "avail_GiB=%.2f swap_used_MiB=%.0f", a/1024/1024, (st-sf)/1024}' /proc/meminfo)
  psi=$(awk 'NR==1 {printf "psi_mem_some10=%s", $2}' /proc/pressure/memory)
  psi_io=$(awk 'NR==1 {printf "psi_io_some10=%s", $2}' /proc/pressure/io)
  docker_mem="docker=?"
  if command -v docker >/dev/null 2>&1; then
    docker_mem=$(timeout 8 docker stats --no-stream --format '{{.Name}}={{.MemUsage}}' comfyui0-runpod 2>/dev/null | tr -d '\n' || echo "docker=?")
  fi
  echo "$(date -Is) $mem dsi=$dsi dso=$dso $psi $psi_io $docker_mem" >>"$LOG"
  sleep 15
done
