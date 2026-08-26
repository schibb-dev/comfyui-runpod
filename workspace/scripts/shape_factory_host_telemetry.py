#!/usr/bin/env python3
"""Lightweight host telemetry snapshots for shape-factory timings."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _read_proc_stat_cpu() -> Optional[Dict[str, float]]:
    text = _read_text(Path("/proc/stat"))
    if not text:
        return None
    for line in text.splitlines():
        if not line.startswith("cpu "):
            continue
        parts = line.split()
        if len(parts) < 8:
            return None
        vals = []
        for tok in parts[1:11]:
            try:
                vals.append(float(tok))
            except Exception:
                vals.append(0.0)
        keys = [
            "user",
            "nice",
            "system",
            "idle",
            "iowait",
            "irq",
            "softirq",
            "steal",
            "guest",
            "guest_nice",
        ]
        out = {k: vals[i] if i < len(vals) else 0.0 for i, k in enumerate(keys)}
        out["total"] = sum(v for k, v in out.items() if k != "total")
        return out
    return None


def _read_meminfo_kb() -> Dict[str, int]:
    text = _read_text(Path("/proc/meminfo"))
    out: Dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        toks = val.strip().split()
        if not toks:
            continue
        try:
            out[key.strip()] = int(float(toks[0]))
        except Exception:
            continue
    return out


def _read_vmstat_subset() -> Dict[str, int]:
    text = _read_text(Path("/proc/vmstat"))
    want = {"pswpin", "pswpout", "pgmajfault"}
    out: Dict[str, int] = {}
    for line in text.splitlines():
        toks = line.split()
        if len(toks) != 2:
            continue
        key = toks[0]
        if key not in want:
            continue
        try:
            out[key] = int(toks[1])
        except Exception:
            continue
    return out


def _read_pressure(kind: str) -> Dict[str, Dict[str, float]]:
    text = _read_text(Path("/proc/pressure") / kind)
    out: Dict[str, Dict[str, float]] = {}
    for line in text.splitlines():
        toks = line.split()
        if not toks:
            continue
        label = toks[0].strip()
        if label not in {"some", "full"}:
            continue
        row: Dict[str, float] = {}
        for tok in toks[1:]:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            try:
                row[k] = float(v)
            except Exception:
                continue
        if row:
            out[label] = row
    return out


def capture_host_snapshot(now_ts: Optional[float] = None) -> Dict[str, Any]:
    ts = float(now_ts) if isinstance(now_ts, (int, float)) else time.time()
    mem = _read_meminfo_kb()
    loadavg = _read_text(Path("/proc/loadavg")).strip().split()
    la = []
    for tok in loadavg[:3]:
        try:
            la.append(float(tok))
        except Exception:
            la.append(0.0)
    return {
        "ts": ts,
        "cpu": _read_proc_stat_cpu(),
        "mem_kb": {
            "total": int(mem.get("MemTotal", 0)),
            "available": int(mem.get("MemAvailable", 0)),
            "free": int(mem.get("MemFree", 0)),
            "cached": int(mem.get("Cached", 0)),
            "buffers": int(mem.get("Buffers", 0)),
            "swap_total": int(mem.get("SwapTotal", 0)),
            "swap_free": int(mem.get("SwapFree", 0)),
        },
        "vmstat": _read_vmstat_subset(),
        "loadavg": {"1m": la[0] if len(la) > 0 else 0.0, "5m": la[1] if len(la) > 1 else 0.0, "15m": la[2] if len(la) > 2 else 0.0},
        "pressure": {
            "cpu": _read_pressure("cpu"),
            "memory": _read_pressure("memory"),
            "io": _read_pressure("io"),
        },
    }


def summarize_cpu_window(start_cpu: Dict[str, Any], end_cpu: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not isinstance(start_cpu, dict) or not isinstance(end_cpu, dict):
        return out
    start_total = float(start_cpu.get("total") or 0.0)
    end_total = float(end_cpu.get("total") or 0.0)
    delta_total = max(0.0, end_total - start_total)
    if delta_total <= 0.0:
        return out
    keys = ("user", "system", "idle", "iowait", "nice", "irq", "softirq", "steal")
    for key in keys:
        dv = float(end_cpu.get(key) or 0.0) - float(start_cpu.get(key) or 0.0)
        out[f"{key}_pct"] = round(max(0.0, dv) * 100.0 / delta_total, 3)
    out["sample_total_jiffies"] = round(delta_total, 3)
    return out
