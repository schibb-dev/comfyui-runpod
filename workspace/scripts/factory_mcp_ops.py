#!/usr/bin/env python3
"""Factory MCP tool implementations — same functions Home / CLI use."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from shape_factory_hourly import (
    _default_data_root,
    _default_og_root,
    _hourly_state_cursor,
    clear_hourly_explore,
    count_factory_pending_submit,
    count_hourly_pending_submit,
    default_hourly_schedule_path,
    hourly_chain_backlogs,
    hourly_explore_active,
    hourly_schedule_status,
    load_hourly_schedule,
    save_hourly_schedule,
    set_hourly_explore,
    simulate_hourly_picks,
)
from shape_factory_reuse_stats import (
    collect_reuse_stats,
    default_reuse_stats_path,
    format_reuse_stats,
)


def _data_root(explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return _default_data_root()


def _jobs_dir(data_root: Path) -> Path:
    return data_root / "shape_factory" / "jobs"


def _slim_simulate(result: Dict[str, Any], *, limit_picks: int = 16) -> Dict[str, Any]:
    picks = result.get("picks") if isinstance(result.get("picks"), list) else []
    slim = []
    for row in picks[:limit_picks]:
        if not isinstance(row, dict):
            continue
        slim.append(
            {
                "index": row.get("index"),
                "cursor": row.get("cursor"),
                "family": row.get("family"),
                "step": row.get("step") or row.get("pick_mode"),
                "reason": row.get("reason"),
                "pick_mode": row.get("pick_mode"),
            }
        )
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        "ok": True,
        "count": result.get("count"),
        "summary": {
            "by_family": summary.get("by_family"),
            "by_step": summary.get("by_step"),
            "seed_count": summary.get("seed_count"),
            "chain_count": summary.get("chain_count"),
        },
        "policy": result.get("policy"),
        "picks": slim,
    }


def hourly_status(*, data_root: Optional[str] = None) -> Dict[str, Any]:
    root = _data_root(data_root)
    status = hourly_schedule_status(data_root=root)
    jobs = _jobs_dir(root)
    try:
        status["factory_pending"] = count_factory_pending_submit(jobs_dir=jobs) if jobs.is_dir() else 0
        status["factory_hourly_pending"] = count_hourly_pending_submit(jobs_dir=jobs) if jobs.is_dir() else 0
    except Exception:
        status.setdefault("factory_pending", None)
        status.setdefault("factory_hourly_pending", None)
    status["sample_cursor"] = _hourly_state_cursor(root)
    status["explore"] = hourly_explore_active(data_root=root)
    sch = status.get("schedule") if isinstance(status.get("schedule"), dict) else {}
    return {
        "ok": True,
        "enabled": bool(sch.get("enabled", True)),
        "interval_minutes": sch.get("interval_minutes"),
        "pending_hourly_min": sch.get("pending_hourly_min"),
        "submit_mode": sch.get("submit_mode"),
        "due": status.get("due"),
        "next_due_at": status.get("next_due_at"),
        "last_tick_at": sch.get("last_tick_at"),
        "sample_cursor": status.get("sample_cursor"),
        "explore": status.get("explore"),
        "still_promo": status.get("still_promo"),
        "faceblast_promo": status.get("faceblast_promo"),
        "factory_pending": status.get("factory_pending"),
        "factory_hourly_pending": status.get("factory_hourly_pending"),
    }


def hourly_simulate(*, count: int = 32, data_root: Optional[str] = None) -> Dict[str, Any]:
    root = _data_root(data_root)
    result = simulate_hourly_picks(int(count) or 32, data_root=root)
    return _slim_simulate(result)


def hourly_explore(
    *,
    action: str = "set",
    kind: str = "family",
    target: str = "",
    family: str = "",
    prompt: str = "",
    still: str = "",
    clip: str = "",
    strength: str = "boost",
    hours: Optional[float] = None,
    ticks: Optional[int] = None,
    until: Optional[str] = None,
    dry_run: bool = False,
    data_root: Optional[str] = None,
) -> Dict[str, Any]:
    root = _data_root(data_root)
    act = str(action or "set").strip().lower()
    if act == "status":
        return {"ok": True, "explore": hourly_explore_active(data_root=root)}
    if act == "clear":
        if dry_run:
            return {"ok": True, "dry_run": True, "explore": None}
        return {**clear_hourly_explore(data_root=root), "dry_run": False}
    built = set_hourly_explore(
        kind=kind,
        target=target,
        family=family,
        prompt=prompt,
        still=still,
        clip=clip,
        strength=strength,
        hours=hours,
        ticks=ticks,
        until=until,
        data_root=root,
        apply=not dry_run,
    )
    built["dry_run"] = bool(dry_run)
    return built


def hourly_schedule_update(
    *,
    enabled: Optional[bool] = None,
    interval_minutes: Optional[int] = None,
    pending_hourly_min: Optional[int] = None,
    submit_mode: Optional[str] = None,
    dry_run: bool = False,
    data_root: Optional[str] = None,
) -> Dict[str, Any]:
    root = _data_root(data_root)
    path = default_hourly_schedule_path(data_root=root)
    sch = load_hourly_schedule(path=path, data_root=root)
    if enabled is not None:
        sch["enabled"] = bool(enabled)
    if interval_minutes is not None:
        sch["interval_minutes"] = int(interval_minutes)
    if pending_hourly_min is not None:
        sch["pending_hourly_min"] = int(pending_hourly_min)
    if submit_mode is not None:
        sch["submit_mode"] = str(submit_mode)
    if dry_run:
        return {"ok": True, "dry_run": True, "schedule": sch}
    save_hourly_schedule(sch, path=path, data_root=root)
    return {**hourly_status(data_root=str(root)), "dry_run": False}


def reuse_stats_summary(*, data_root: Optional[str] = None, refresh: bool = False) -> Dict[str, Any]:
    root = _data_root(data_root)
    snap = default_reuse_stats_path(data_root=root)
    payload: Dict[str, Any]
    if refresh or not snap.is_file():
        jobs = _jobs_dir(root)
        payload = collect_reuse_stats(jobs)
    else:
        try:
            payload = json.loads(snap.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": "reuse_stats_unreadable", "detail": str(exc)}
    guide = payload.get("guide_hourly") if isinstance(payload.get("guide_hourly"), dict) else {}
    return {
        "ok": True,
        "jobs_scanned": payload.get("jobs_scanned"),
        "jobs_hourly": payload.get("jobs_hourly"),
        "jobs_operator": payload.get("jobs_operator"),
        "jobs_favored": payload.get("jobs_favored"),
        "optimal_seed_unit": (payload.get("optimal_unit") or {}).get("recommended")
        if isinstance(payload.get("optimal_unit"), dict)
        else None,
        "optimal_recipe_unit": (payload.get("optimal_recipe_unit") or {}).get("recommended")
        if isinstance(payload.get("optimal_recipe_unit"), dict)
        else None,
        "guide_source": guide.get("source"),
        "suggested_seed_family_weights": (guide.get("suggested_seed_family_weights") or [])[:8],
        "table": format_reuse_stats(payload)[:4000],
    }


def mark_appetite(
    *,
    relpath: str,
    appetite: str,
    dry_run: bool = True,
    data_root: Optional[str] = None,
) -> Dict[str, Any]:
    from shape_factory_ratings import (
        default_appetite_index_path,
        normalize_appetite,
        set_output_appetite,
    )

    root = _data_root(data_root)
    rel = str(relpath or "").strip().replace("\\", "/").lstrip("/")
    if rel.startswith("output/"):
        rel = rel[len("output/") :]
    want = normalize_appetite(appetite)
    og = _default_og_root(root)
    candidates = [
        og / rel.replace("og/", "", 1) if rel.startswith("og/") else og / rel,
        root / "output" / rel,
        Path("/home/yuji/comfyui-runpod-data/output") / rel,
    ]
    media = next((p for p in candidates if p.is_file()), None)
    if media is None:
        return {"ok": False, "error": "media_missing", "relpath": rel, "tried": [str(p) for p in candidates]}
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "relpath": rel,
            "appetite": want,
            "media_abs": str(media),
        }
    saved = set_output_appetite(
        media_abs=media,
        media_relpath=rel if rel.startswith("og/") else f"og/{rel}",
        appetite=want,
        og_root=og if og.is_dir() else media.parent,
        appetite_index_path=default_appetite_index_path(og if og.is_dir() else media.parent),
    )
    saved["dry_run"] = False
    return saved


def hourly_backlog(*, data_root: Optional[str] = None) -> Dict[str, Any]:
    root = _data_root(data_root)
    raw = hourly_chain_backlogs(data_root=root)
    chains = []
    for chain in raw.get("chains") or []:
        if not isinstance(chain, dict):
            continue
        nxt = chain.get("next") if isinstance(chain.get("next"), dict) else {}
        chains.append(
            {
                "id": chain.get("id"),
                "label": chain.get("label"),
                "count": chain.get("count"),
                "by_family": chain.get("by_family"),
                "due_this_cursor": chain.get("due_this_cursor"),
                "next_job": nxt.get("job_key") or nxt.get("label"),
            }
        )
    return {"ok": True, "cursor": raw.get("cursor"), "chains": chains, "note": raw.get("note")}


def queue_snapshot(*, data_root: Optional[str] = None) -> Dict[str, Any]:
    root = _data_root(data_root)
    jobs = _jobs_dir(root)
    pending = count_factory_pending_submit(jobs_dir=jobs) if jobs.is_dir() else 0
    hourly_pending = count_hourly_pending_submit(jobs_dir=jobs) if jobs.is_dir() else 0
    waiting = None
    running = None
    comfy = os.environ.get("COMFY_SERVER") or os.environ.get("COMFYUI_URL") or "http://127.0.0.1:8188"
    try:
        req = urllib.request.Request(f"{comfy.rstrip('/')}/queue", method="GET")
        with urllib.request.urlopen(req, timeout=4) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        waiting = len(body.get("queue_pending") or [])
        running = len(body.get("queue_running") or [])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        pass
    return {
        "ok": True,
        "comfy_waiting": waiting,
        "comfy_running": running,
        "factory_pending": pending,
        "factory_hourly_pending": hourly_pending,
    }


def dispatch_tool(name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    if name == "hourly_status":
        return hourly_status(data_root=args.get("data_root"))
    if name == "hourly_simulate":
        return hourly_simulate(count=int(args.get("count") or 32), data_root=args.get("data_root"))
    if name == "hourly_explore":
        return hourly_explore(
            action=str(args.get("action") or "set"),
            kind=str(args.get("kind") or "family"),
            target=str(args.get("target") or ""),
            family=str(args.get("family") or args.get("target") or ""),
            prompt=str(args.get("prompt") or ""),
            still=str(args.get("still") or ""),
            clip=str(args.get("clip") or ""),
            strength=str(args.get("strength") or "boost"),
            hours=args.get("hours"),
            ticks=args.get("ticks"),
            until=args.get("until"),
            dry_run=bool(args.get("dry_run", False)),
            data_root=args.get("data_root"),
        )
    if name == "hourly_schedule":
        return hourly_schedule_update(
            enabled=args.get("enabled"),
            interval_minutes=args.get("interval_minutes"),
            pending_hourly_min=args.get("pending_hourly_min"),
            submit_mode=args.get("submit_mode"),
            dry_run=bool(args.get("dry_run", False)),
            data_root=args.get("data_root"),
        )
    if name == "reuse_stats_summary":
        return reuse_stats_summary(
            data_root=args.get("data_root"),
            refresh=bool(args.get("refresh", False)),
        )
    if name == "mark_appetite":
        return mark_appetite(
            relpath=str(args.get("relpath") or ""),
            appetite=str(args.get("appetite") or ""),
            dry_run=bool(args.get("dry_run", True)),
            data_root=args.get("data_root"),
        )
    if name == "hourly_backlog":
        return hourly_backlog(data_root=args.get("data_root"))
    if name == "queue_snapshot":
        return queue_snapshot(data_root=args.get("data_root"))
    return {"ok": False, "error": "unknown_tool", "tool": name}


TOOL_SPECS = [
    {
        "name": "hourly_status",
        "description": "Hourly enabled/interval/floor, active explore overlay, last tick.",
        "inputSchema": {
            "type": "object",
            "properties": {"data_root": {"type": "string"}},
        },
    },
    {
        "name": "hourly_simulate",
        "description": "Dry-run the next N hourly picks (family/phase/reason). Respects explore.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "default": 32},
                "data_root": {"type": "string"},
            },
        },
    },
    {
        "name": "hourly_explore",
        "description": (
            "Set or clear a time-boxed explore overlay (family/prompt/still/clip). "
            "No appetite prior required. action=set|clear|status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["set", "clear", "status"], "default": "set"},
                "kind": {"type": "string", "enum": ["family", "prompt", "still", "clip"]},
                "target": {"type": "string"},
                "family": {"type": "string"},
                "prompt": {"type": "string"},
                "still": {"type": "string"},
                "clip": {"type": "string"},
                "strength": {"type": "string", "enum": ["boost", "focus"], "default": "boost"},
                "hours": {"type": "number"},
                "ticks": {"type": "integer"},
                "until": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
                "data_root": {"type": "string"},
            },
        },
    },
    {
        "name": "hourly_schedule",
        "description": "Pause/resume hourlies, set interval or pending hourly floor. Not a GPU enqueue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "interval_minutes": {"type": "integer"},
                "pending_hourly_min": {"type": "integer"},
                "submit_mode": {"type": "string", "enum": ["auto", "comfy", "pending"]},
                "dry_run": {"type": "boolean", "default": False},
                "data_root": {"type": "string"},
            },
        },
    },
    {
        "name": "reuse_stats_summary",
        "description": "Read-only reuse / appetite mix (operator vs hourly).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {"type": "boolean", "default": False},
                "data_root": {"type": "string"},
            },
        },
    },
    {
        "name": "mark_appetite",
        "description": "Set appetite more/fast_track/less/remove on an output or still. dry_run defaults true.",
        "inputSchema": {
            "type": "object",
            "required": ["relpath", "appetite"],
            "properties": {
                "relpath": {"type": "string"},
                "appetite": {"type": "string", "enum": ["more", "fast_track", "less", "remove", "neutral", ""]},
                "dry_run": {"type": "boolean", "default": True},
                "data_root": {"type": "string"},
            },
        },
    },
    {
        "name": "hourly_backlog",
        "description": "Facial and i2v→GEX computed waiting lists (preview, not cull).",
        "inputSchema": {
            "type": "object",
            "properties": {"data_root": {"type": "string"}},
        },
    },
    {
        "name": "queue_snapshot",
        "description": "Comfy waiting/running plus factory pending counts. No raw prompt graphs.",
        "inputSchema": {
            "type": "object",
            "properties": {"data_root": {"type": "string"}},
        },
    },
]
