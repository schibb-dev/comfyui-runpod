#!/usr/bin/env bash
# Collect WSL swap + disk I/O telemetry (baseline + auto-stress sampling).
#
# Writes JSONL to --out (one record per sample) so you can correlate with
# “Claude felt slow” timestamps later.
#
# Usage examples:
#   ./scripts/wsl_swap_io_telemetry.sh
#   ./scripts/wsl_swap_io_telemetry.sh --out /tmp/swap_io.jsonl --baseline-interval 15 --stress-interval 2
#   ./scripts/wsl_swap_io_telemetry.sh --out ./.data/telemetry/swap_io_telemetry.jsonl >/tmp/swap_io.nohup.log 2>&1 &
#
# Daily rotation: --out swap_io_telemetry.jsonl → swap_io_telemetry_YYYYMMDD.jsonl
# in the same directory (local timezone). Use --no-daily-rotate for a fixed filename.
#
# Notes:
# - Swap deltas come from /proc/vmstat pswpin/pswpout (pages swapped).
# - Disk I/O rate is computed from /proc/diskstats sector counters (summed across block devices).
# - “Stress mode” triggers when swap-in/out delta exceeds a threshold OR disk I/O exceeds a threshold.
set -euo pipefail

BASELINE_INTERVAL_S=15
STRESS_INTERVAL_S=2
HOLD_STRESS_S=90

# Swap-in/out trigger thresholds (MiB per sample interval).
SWAP_IN_TRIGGER_MIB=128
SWAP_OUT_TRIGGER_MIB=64

# Disk I/O trigger thresholds (MiB/s).
# (Computed from delta sectors / dt)
DISK_READ_TRIGGER_MIBPS=200
DISK_WRITE_TRIGGER_MIBPS=50

OUT=""
OUT_TEMPLATE=""
DAILY_ROTATE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_TEMPLATE="${2:-}"; shift 2 ;;
    --no-daily-rotate) DAILY_ROTATE=0; shift ;;
    --baseline-interval) BASELINE_INTERVAL_S="${2:-}"; shift 2 ;;
    --stress-interval) STRESS_INTERVAL_S="${2:-}"; shift 2 ;;
    --hold-stress) HOLD_STRESS_S="${2:-}"; shift 2 ;;
    --swap-in-trigger-mib) SWAP_IN_TRIGGER_MIB="${2:-}"; shift 2 ;;
    --swap-out-trigger-mib) SWAP_OUT_TRIGGER_MIB="${2:-}"; shift 2 ;;
    --disk-read-trigger-mibps) DISK_READ_TRIGGER_MIBPS="${2:-}"; shift 2 ;;
    --disk-write-trigger-mibps) DISK_WRITE_TRIGGER_MIBPS="${2:-}"; shift 2 ;;
    --help|-h)
      echo "Usage: $0 [--out FILE] [--no-daily-rotate] [--baseline-interval N] [--stress-interval N] [--hold-stress N]"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

resolve_out_path() {
  local day="${1:-$(date +%Y%m%d)}"
  if [[ -z "$OUT_TEMPLATE" ]]; then
    echo "/tmp/wsl_swap_io_telemetry_${day}.jsonl"
    return
  fi
  if [[ "$DAILY_ROTATE" -eq 0 ]]; then
    echo "$OUT_TEMPLATE"
    return
  fi
  local dir base stem
  dir="$(dirname "$OUT_TEMPLATE")"
  base="$(basename "$OUT_TEMPLATE")"
  if [[ "$base" == *.jsonl ]]; then
    stem="${base%.jsonl}"
  else
    stem="$base"
  fi
  if [[ "$stem" =~ _[0-9]{8}$ ]]; then
    echo "$OUT_TEMPLATE"
    return
  fi
  echo "$dir/${stem}_${day}.jsonl"
}

migrate_legacy_log() {
  [[ -z "$OUT_TEMPLATE" || "$DAILY_ROTATE" -eq 0 ]] && return 0
  local legacy dir base
  legacy="$OUT_TEMPLATE"
  [[ -f "$legacy" ]] || return 0
  dir="$(dirname "$legacy")"
  base="$(basename "$legacy")"
  [[ "$base" == "swap_io_telemetry.jsonl" ]] || return 0
  local dated legacy_day
  legacy_day="$(date -r "$legacy" +%Y%m%d 2>/dev/null || date +%Y%m%d)"
  dated="$(resolve_out_path "$legacy_day")"
  if [[ ! -f "$dated" ]]; then
    mv "$legacy" "$dated"
  fi
}

ensure_out_file() {
  local new_out prev="$OUT"
  new_out="$(resolve_out_path)"
  if [[ "$new_out" == "$OUT" ]]; then
    return 0
  fi
  mkdir -p "$(dirname "$new_out")"
  touch "$new_out"
  if [[ -n "$prev" && "$prev" != "$new_out" ]]; then
    echo "{\"ts\":\"$(date -Is)\",\"mode\":\"baseline\",\"event\":\"rotate\",\"from\":\"$prev\",\"to\":\"$new_out\"}" >>"$new_out"
  elif [[ ! -s "$new_out" ]]; then
    echo "{\"ts\":\"$(date -Is)\",\"mode\":\"baseline\",\"event\":\"start\",\"out\":\"$new_out\"}" >>"$new_out"
  fi
  OUT="$new_out"
}

OUT=""
migrate_legacy_log
ensure_out_file

page_size_bytes="$(getconf PAGESIZE 2>/dev/null || echo 4096)"
page_size_mib="$(python3 - <<PY
import os
ps=int(os.environ.get("PSIZE", str(4096)))
print(ps/(1024*1024))
PY
)" 2>/dev/null || page_size_mib="0.00390625"

read_swap_pages() {
  # pswpin/pswpout are in pages.
  # shellcheck disable=SC2002
  awk '
    $1=="pswpin"{pin=$2}
    $1=="pswpout"{pout=$2}
    END{print pin, pout}
  ' /proc/vmstat
}

read_disk_sectors_rw() {
  # Sum sectors read/written across common block device families.
  # /proc/diskstats fields:
  #  1 major,2 minor,3 name, ... 6 sectors read, ... 10 sectors written
  awk '
    $3 ~ /^(sd|nvme|vd|xvd|mmcblk)/ {
      r += $6;
      w += $10;
    }
    END { print r, w }
  ' /proc/diskstats
}

read_psi_avg10_some() {
  # Returns: mem_some10 io_some10 (strings like "0.12")
  local mem io
  mem="$(awk '
    /^some / {
      for(i=1;i<=NF;i++) if($i ~ /^avg10=/) { sub("avg10=","",$i); print $i; exit }
    }' /proc/pressure/memory 2>/dev/null || echo "0")"
  io="$(awk '
    /^some / {
      for(i=1;i<=NF;i++) if($i ~ /^avg10=/) { sub("avg10=","",$i); print $i; exit }
    }' /proc/pressure/io 2>/dev/null || echo "0")"
  echo "$mem $io"
}

read_swap_used_mib() {
  awk '
    $1=="SwapTotal:"{t=$2}
    $1=="SwapFree:"{f=$2}
    END{
      if(t==0){print 0; exit}
      used=t-f;
      # $2 is in kB
      print used/1024
    }' /proc/meminfo
}

docker_mem_usage() {
  if command -v docker >/dev/null 2>&1; then
    timeout 8 docker stats --no-stream --format '{{.Name}}={{.MemUsage}}' comfyui0-runpod 2>/dev/null \
      | tr -d '\n' || echo "docker=none"
  else
    echo "docker=none"
  fi
}

epoch_now() { date +%s; }

last_epoch="$(epoch_now)"
read -r last_pswpin last_pswpout < <(read_swap_pages)
read -r last_disk_r last_disk_w < <(read_disk_sectors_rw)
last_swap_used_mib="$(read_swap_used_mib)"

mode="baseline"
stress_until_epoch=0

while true; do
  ensure_out_file
  now_epoch="$(epoch_now)"
  dt_s=$(( now_epoch - last_epoch ))
  if [[ "$dt_s" -le 0 ]]; then
    dt_s=1
  fi

  read -r pswpin pswpout < <(read_swap_pages)
  dpswpin_pages=$(( pswpin - last_pswpin ))
  dpswpout_pages=$(( pswpout - last_pswpout ))
  if [[ "$dpswpin_pages" -lt 0 ]]; then dpswpin_pages=0; fi
  if [[ "$dpswpout_pages" -lt 0 ]]; then dpswpout_pages=0; fi

  # Convert pages to MiB (kB page ~ 4kB default).
  dswap_in_mib="$(awk -v p="$dpswpin_pages" -v psb="$page_size_bytes" 'BEGIN{ printf "%.2f", (p*psb)/(1024*1024) }')"
  dswap_out_mib="$(awk -v p="$dpswpout_pages" -v psb="$page_size_bytes" 'BEGIN{ printf "%.2f", (p*psb)/(1024*1024) }')"

  swap_used_mib="$(read_swap_used_mib)"

  read -r disk_r disk_w < <(read_disk_sectors_rw)
  dsectors_r=$(( disk_r - last_disk_r ))
  dsectors_w=$(( disk_w - last_disk_w ))
  if [[ "$dsectors_r" -lt 0 ]]; then dsectors_r=0; fi
  if [[ "$dsectors_w" -lt 0 ]]; then dsectors_w=0; fi

  # sectors are 512-byte units on Linux
  disk_read_mibps="$(awk -v ds="$dsectors_r" -v dt="$dt_s" 'BEGIN{ printf "%.2f", (ds*512)/(1024*1024*dt) }')"
  disk_write_mibps="$(awk -v ds="$dsectors_w" -v dt="$dt_s" 'BEGIN{ printf "%.2f", (ds*512)/(1024*1024*dt) }')"

  read -r psi_mem_some10 psi_io_some10 < <(read_psi_avg10_some)

  docker_mem="$(docker_mem_usage)"

  ts="$(date -Is)"

  is_stress="false"
  if awk -v x="$dswap_in_mib" -v th="$SWAP_IN_TRIGGER_MIB" 'BEGIN{exit !(x>=th)}'; then
    is_stress="true"
  fi
  if awk -v x="$dswap_out_mib" -v th="$SWAP_OUT_TRIGGER_MIB" 'BEGIN{exit !(x>=th)}'; then
    is_stress="true"
  fi
  if awk -v x="$disk_read_mibps" -v th="$DISK_READ_TRIGGER_MIBPS" 'BEGIN{exit !(x>=th)}'; then
    is_stress="true"
  fi
  if awk -v x="$disk_write_mibps" -v th="$DISK_WRITE_TRIGGER_MIBPS" 'BEGIN{exit !(x>=th)}'; then
    is_stress="true"
  fi

  if [[ "$is_stress" == "true" ]]; then
    stress_until_epoch=$(( now_epoch + HOLD_STRESS_S ))
    mode="stress"
  else
    if [[ "$now_epoch" -ge "$stress_until_epoch" ]]; then
      mode="baseline"
    else
      mode="stress"
    fi
  fi

  # Log one JSONL record.
  echo "{\"ts\":\"$ts\",\"mode\":\"$mode\",\"dt_s\":$dt_s,\"swap_used_mib\":$swap_used_mib,\"dswap_in_mib\":$dswap_in_mib,\"dswap_out_mib\":$dswap_out_mib,\"psi_mem_some10\":$psi_mem_some10,\"psi_io_some10\":$psi_io_some10,\"disk_read_mibps\":$disk_read_mibps,\"disk_write_mibps\":$disk_write_mibps,\"docker_comfy_mem\":\"$docker_mem\"}" >>"$OUT"

  last_epoch="$now_epoch"
  last_pswpin="$pswpin"
  last_pswpout="$pswpout"
  last_disk_r="$disk_r"
  last_disk_w="$disk_w"
  last_swap_used_mib="$swap_used_mib"

  if [[ "$mode" == "stress" ]]; then
    sleep "$STRESS_INTERVAL_S"
  else
    sleep "$BASELINE_INTERVAL_S"
  fi
done

