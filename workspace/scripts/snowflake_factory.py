#!/usr/bin/env python3
"""
Mini snowflake factory spike.

This is intentionally small:
- first-class asset/workflow buckets
- run plans that connect input asset bucket -> workflow bucket -> output asset bucket
- placeholder rules attached to run plans
- compatibility preview
- generation of ComfyUI review workflow JSONs and planned output bucket entries

It does not submit jobs to ComfyUI yet.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

from snowflake_inventory import graph_fingerprint, is_litegraph_workflow, read_json
from output_path_lib import flatten_output_prefix


DEFAULT_DB = "/home/yuji/comfyui-runpod-data/comfyui_user/default/snowflake_factory.sqlite"
DEFAULT_WORKFLOW_DIR = "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/factory"
DEFAULT_INPUT_ROOT = "/home/yuji/comfyui-runpod-data/input"
DEFAULT_OUTPUT_PREFIX_ROOT = "workflow-review/%date:yyyy-MM-dd%"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}


def utc_now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def slug(value: str, limit: int = 80) -> str:
    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")
    return (out or "item")[:limit]


def workflow_stem(path: Path) -> str:
    stem = path.stem
    if stem.endswith(".workflow"):
        stem = stem[: -len(".workflow")]
    return stem


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads_maybe(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(path)


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS buckets (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            bucket_type TEXT NOT NULL CHECK(bucket_type IN ('asset', 'workflow')),
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_items (
            id INTEGER PRIMARY KEY,
            bucket_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            media_type TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'available',
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(bucket_id, path),
            FOREIGN KEY(bucket_id) REFERENCES buckets(id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_items (
            id INTEGER PRIMARY KEY,
            bucket_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            workflow_type TEXT NOT NULL,
            graph_hash TEXT,
            input_contract_json TEXT NOT NULL,
            output_contract_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(bucket_id, path),
            FOREIGN KEY(bucket_id) REFERENCES buckets(id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS run_plans (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            input_bucket_id INTEGER NOT NULL,
            workflow_bucket_id INTEGER NOT NULL,
            output_bucket_id INTEGER NOT NULL,
            rules_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(input_bucket_id) REFERENCES buckets(id),
            FOREIGN KEY(workflow_bucket_id) REFERENCES buckets(id),
            FOREIGN KEY(output_bucket_id) REFERENCES buckets(id)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS planned_jobs (
            id INTEGER PRIMARY KEY,
            run_plan_id INTEGER NOT NULL,
            asset_item_id INTEGER NOT NULL,
            workflow_item_id INTEGER NOT NULL,
            output_asset_item_id INTEGER,
            job_key TEXT NOT NULL,
            status TEXT NOT NULL,
            generated_workflow_path TEXT,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_plan_id, job_key),
            FOREIGN KEY(run_plan_id) REFERENCES run_plans(id),
            FOREIGN KEY(asset_item_id) REFERENCES asset_items(id),
            FOREIGN KEY(workflow_item_id) REFERENCES workflow_items(id),
            FOREIGN KEY(output_asset_item_id) REFERENCES asset_items(id)
        )
        """
    )
    return con


def media_type_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext == ".json":
        return "json"
    return "unknown"


def role_for_media_type(media_type: str) -> str:
    if media_type == "image":
        return "source_image"
    if media_type == "video":
        return "source_video"
    return "source_asset"


def get_bucket(con: sqlite3.Connection, name: str, bucket_type: Optional[str] = None) -> sqlite3.Row:
    if bucket_type:
        row = con.execute("SELECT * FROM buckets WHERE name = ? AND bucket_type = ?", (name, bucket_type)).fetchone()
    else:
        row = con.execute("SELECT * FROM buckets WHERE name = ?", (name,)).fetchone()
    if row is None:
        expected = f" {bucket_type}" if bucket_type else ""
        raise RuntimeError(f"no{expected} bucket named {name!r}")
    return row


def default_rules() -> dict[str, Any]:
    return {
        "version": 1,
        "rules": [
            {
                "type": "priority_queueing",
                "name": "submission_order",
                "description": "Eligible jobs are ordered by asset path, then workflow path.",
            },
            {
                "type": "parameter_adjustment",
                "name": "none",
                "description": "No parameter expansion in the initial spike.",
            },
            {
                "type": "completion_target",
                "name": "process_each_input_once",
                "description": "The plan is complete when each compatible input/workflow pair has a planned job.",
            },
        ],
    }


def workflow_contract(workflow: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    inputs: set[str] = set()
    outputs: set[str] = set()
    input_nodes: list[dict[str, Any]] = []
    output_nodes: list[dict[str, Any]] = []

    if not is_litegraph_workflow(workflow):
        return {"media_types": ["unknown"], "nodes": []}, {"media_types": ["unknown"], "nodes": []}

    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "")
        mode = node.get("mode", 0)
        title = node.get("title")
        if node_type in {"LoadImage", "LoadImageWithFilename|pysssss"}:
            inputs.add("image")
            input_nodes.append({"id": node.get("id"), "type": node_type, "title": title})
        if node_type in {"VHS_LoadVideo", "VHS_LoadVideoPath", "VHS_LoadVideoFFmpeg", "VHS_LoadVideoFFmpegPath"}:
            inputs.add("video")
            input_nodes.append({"id": node.get("id"), "type": node_type, "title": title})
        if mode in (2, 4):
            continue
        if node_type == "VHS_VideoCombine":
            widgets = node.get("widgets_values")
            if not isinstance(widgets, dict) or widgets.get("save_output") is not False:
                outputs.add("video")
                output_nodes.append({"id": node.get("id"), "type": node_type, "title": title})
        if node_type == "SaveImage":
            outputs.add("image")
            output_nodes.append({"id": node.get("id"), "type": node_type, "title": title})

    return {
        "media_types": sorted(inputs) or ["unknown"],
        "nodes": input_nodes,
    }, {
        "media_types": sorted(outputs) or ["unknown"],
        "nodes": output_nodes,
    }


def compatible(asset_row: sqlite3.Row, workflow_row: sqlite3.Row) -> tuple[bool, str]:
    media_type = str(asset_row["media_type"])
    contract = json_loads_maybe(workflow_row["input_contract_json"], {})
    accepted = set(str(v) for v in contract.get("media_types") or [])
    if media_type in accepted:
        return True, f"asset media_type {media_type!r} satisfies workflow input contract"
    return False, f"asset media_type {media_type!r} not in workflow input contract {sorted(accepted)}"


def load_run_plan(con: sqlite3.Connection, name: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT
            rp.*,
            ib.name AS input_bucket_name,
            wb.name AS workflow_bucket_name,
            ob.name AS output_bucket_name
        FROM run_plans rp
        JOIN buckets ib ON ib.id = rp.input_bucket_id
        JOIN buckets wb ON wb.id = rp.workflow_bucket_id
        JOIN buckets ob ON ob.id = rp.output_bucket_id
        WHERE rp.name = ?
        """,
        (name,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"no run plan named {name!r}")
    return dict(row)


def eligible_jobs(con: sqlite3.Connection, plan_name: str) -> list[dict[str, Any]]:
    plan = load_run_plan(con, plan_name)
    assets = list(
        con.execute(
            """
            SELECT ai.*, b.name AS bucket_name
            FROM asset_items ai
            JOIN buckets b ON b.id = ai.bucket_id
            WHERE ai.bucket_id = ?
            ORDER BY ai.path
            """,
            (plan["input_bucket_id"],),
        )
    )
    workflows = list(
        con.execute(
            """
            SELECT wi.*, b.name AS bucket_name
            FROM workflow_items wi
            JOIN buckets b ON b.id = wi.bucket_id
            WHERE wi.bucket_id = ?
            ORDER BY wi.path
            """,
            (plan["workflow_bucket_id"],),
        )
    )
    jobs: list[dict[str, Any]] = []
    for asset in assets:
        for workflow in workflows:
            ok, reason = compatible(asset, workflow)
            jobs.append(
                {
                    "eligible": ok,
                    "reason": reason,
                    "asset": dict(asset),
                    "workflow": dict(workflow),
                    "plan": plan,
                }
            )
    return jobs


def relative_input_name(path: Path, input_root: Path) -> tuple[str, Optional[str]]:
    try:
        rel = path.resolve().relative_to(input_root.resolve())
        return rel.as_posix(), None
    except Exception:
        return path.name, f"asset is outside ComfyUI input root; using basename {path.name!r}"


def apply_asset_to_workflow(workflow: Any, asset_path: Path, input_root: Path) -> tuple[Any, list[str]]:
    draft = json.loads(json.dumps(workflow))
    warnings: list[str] = []
    input_name, warning = relative_input_name(asset_path, input_root)
    if warning:
        warnings.append(warning)

    if not is_litegraph_workflow(draft):
        raise RuntimeError("workflow is not a LiteGraph workflow")

    applied = 0
    for node in draft.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "")
        if node_type == "LoadImageWithFilename|pysssss":
            node["type"] = "LoadImage"
            props = node.get("properties")
            if isinstance(props, dict):
                props["Node name for S&R"] = "LoadImage"
            outputs = node.get("outputs")
            if isinstance(outputs, list) and len(outputs) > 2:
                node["outputs"] = outputs[:2]
            node_type = "LoadImage"
        if node_type == "LoadImage":
            widgets = node.get("widgets_values")
            if isinstance(widgets, list):
                if widgets:
                    widgets[0] = input_name
                else:
                    widgets.append(input_name)
                if len(widgets) == 1:
                    widgets.append("image")
            else:
                node["widgets_values"] = [input_name, "image"]
            applied += 1

    if applied == 0:
        warnings.append("no LoadImage node was updated")
    return draft, warnings


def strip_video_previews_and_redirect_outputs(
    workflow: Any,
    output_prefix: str,
    *,
    final_node_ids: Optional[set[int]] = None,
) -> dict[str, int]:
    """Keep only final video saves; mute preview/debug/raw combines so they are never stored."""
    changes = {
        "stripped_video_previews": 0,
        "redirected_outputs": 0,
        "disabled_non_final_outputs": 0,
    }
    flat_prefix = flatten_output_prefix(str(output_prefix or "").rstrip("/"))
    finals = {int(x) for x in (final_node_ids or set()) if str(x).strip() != ""}

    def _is_non_final_title(title: str) -> bool:
        t = str(title or "").lower()
        return any(k in t for k in ("preview", "debug", "raw", "sample frame", "interpoled", "upscaled", "upint"))

    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "")
        if node_type not in {"VHS_VideoCombine", "SaveImage"}:
            continue
        widgets = node.get("widgets_values")
        if node_type == "VHS_VideoCombine" and not isinstance(widgets, dict):
            continue
        if isinstance(widgets, dict) and "videopreview" in widgets:
            widgets.pop("videopreview", None)
            changes["stripped_video_previews"] += 1

        try:
            nid = int(node.get("id"))
        except (TypeError, ValueError):
            nid = -1
        title = str(node.get("title") or "")
        is_final = (nid in finals) if finals else (
            node_type == "VHS_VideoCombine"
            and node.get("mode", 0) not in (2, 4)
            and not _is_non_final_title(title)
            and (not isinstance(widgets, dict) or widgets.get("save_output") is not False)
        )
        # When finals are known, only those save. Otherwise mute preview/raw titles.
        if finals:
            keep = nid in finals
        else:
            keep = is_final and not _is_non_final_title(title)

        if keep and node_type == "VHS_VideoCombine" and isinstance(widgets, dict):
            if node.get("mode", 0) not in (2, 4) and widgets.get("save_output") is not False:
                widgets["filename_prefix"] = flat_prefix
                widgets["save_metadata"] = True
                widgets["save_output"] = True
                node["mode"] = 0
                changes["redirected_outputs"] += 1
            continue

        # Mute / never-store for preview, debug, raw, and other non-final savers.
        if node_type == "VHS_VideoCombine" and isinstance(widgets, dict):
            if widgets.get("save_output") is not False or node.get("mode", 0) not in (2, 4):
                widgets["save_output"] = False
                node["mode"] = 2
                if title and not title.upper().startswith("DISABLED"):
                    node["title"] = f"DISABLED OUTPUT: {title}"
                changes["disabled_non_final_outputs"] += 1
        elif node_type == "SaveImage" and node.get("mode", 0) not in (2, 4):
            node["mode"] = 2
            if title and not title.upper().startswith("DISABLED"):
                node["title"] = f"DISABLED OUTPUT: {title}"
            changes["disabled_non_final_outputs"] += 1
    return changes


def cmd_bucket_create(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    metadata = json_loads_maybe(args.metadata_json, {}) if args.metadata_json else {}
    now = utc_now()
    con.execute(
        """
        INSERT INTO buckets (name, bucket_type, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            bucket_type=excluded.bucket_type,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (args.name, args.type, json_dumps(metadata), now, now),
    )
    con.commit()
    print(f"bucket={args.name}")
    print(f"type={args.type}")
    return 0


def cmd_bucket_list(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    rows = con.execute(
        """
        SELECT
            b.*,
            (SELECT COUNT(*) FROM asset_items ai WHERE ai.bucket_id = b.id) AS asset_count,
            (SELECT COUNT(*) FROM workflow_items wi WHERE wi.bucket_id = b.id) AS workflow_count
        FROM buckets b
        ORDER BY b.bucket_type, b.name
        """
    ).fetchall()
    for row in rows:
        count = row["asset_count"] if row["bucket_type"] == "asset" else row["workflow_count"]
        print(f"- {row['bucket_type']} `{row['name']}` items={count}")
    return 0


def cmd_bucket_show(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    bucket = get_bucket(con, args.name)
    print(f"# Bucket `{bucket['name']}`")
    print(f"- type: {bucket['bucket_type']}")
    print(f"- metadata: {bucket['metadata_json']}")
    if bucket["bucket_type"] == "asset":
        rows = con.execute(
            "SELECT * FROM asset_items WHERE bucket_id = ? ORDER BY path",
            (bucket["id"],),
        ).fetchall()
        for row in rows:
            print(f"- asset id={row['id']} media={row['media_type']} role={row['role']} status={row['status']} path=`{row['path']}`")
    else:
        rows = con.execute(
            "SELECT * FROM workflow_items WHERE bucket_id = ? ORDER BY path",
            (bucket["id"],),
        ).fetchall()
        for row in rows:
            print(
                f"- workflow id={row['id']} type={row['workflow_type']} graph={row['graph_hash']} "
                f"inputs={row['input_contract_json']} outputs={row['output_contract_json']} path=`{row['path']}`"
            )
    return 0


def cmd_bucket_add_asset(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    bucket = get_bucket(con, args.bucket, "asset")
    path = Path(args.path).expanduser().resolve()
    if not path.exists() and not args.allow_missing:
        raise RuntimeError(f"asset path does not exist: {path}")
    media_type = args.media_type or media_type_for_path(path)
    role = args.role or role_for_media_type(media_type)
    metadata = {
        "added_by": "snowflake_factory",
        "exists_at_add": path.exists(),
    }
    now = utc_now()
    con.execute(
        """
        INSERT INTO asset_items (bucket_id, path, media_type, role, status, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bucket_id, path) DO UPDATE SET
            media_type=excluded.media_type,
            role=excluded.role,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (bucket["id"], str(path), media_type, role, "available", json_dumps(metadata), now, now),
    )
    con.commit()
    print(f"asset_bucket={bucket['name']}")
    print(f"asset={path}")
    print(f"media_type={media_type}")
    print(f"role={role}")
    return 0


def cmd_bucket_add_workflow(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    bucket = get_bucket(con, args.bucket, "workflow")
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"workflow path does not exist: {path}")
    workflow = read_json(path)
    if not is_litegraph_workflow(workflow):
        raise RuntimeError(f"not a LiteGraph workflow: {path}")
    input_contract, output_contract = workflow_contract(workflow)
    graph_hash = graph_fingerprint(workflow)
    metadata = {
        "added_by": "snowflake_factory",
        "node_count": len(workflow.get("nodes") or []),
        "link_count": len(workflow.get("links") or []),
    }
    now = utc_now()
    con.execute(
        """
        INSERT INTO workflow_items (
            bucket_id, path, workflow_type, graph_hash, input_contract_json, output_contract_json,
            metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bucket_id, path) DO UPDATE SET
            workflow_type=excluded.workflow_type,
            graph_hash=excluded.graph_hash,
            input_contract_json=excluded.input_contract_json,
            output_contract_json=excluded.output_contract_json,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            bucket["id"],
            str(path),
            args.workflow_type,
            graph_hash,
            json_dumps(input_contract),
            json_dumps(output_contract),
            json_dumps(metadata),
            now,
            now,
        ),
    )
    con.commit()
    print(f"workflow_bucket={bucket['name']}")
    print(f"workflow={path}")
    print(f"graph_hash={graph_hash}")
    print(f"inputs={json_dumps(input_contract)}")
    print(f"outputs={json_dumps(output_contract)}")
    return 0


def cmd_run_plan_create(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    input_bucket = get_bucket(con, args.input_bucket, "asset")
    workflow_bucket = get_bucket(con, args.workflow_bucket, "workflow")
    output_bucket = get_bucket(con, args.output_bucket, "asset")
    rules = json_loads_maybe(args.rules_json, default_rules()) if args.rules_json else default_rules()
    metadata = {"created_by": "snowflake_factory"}
    now = utc_now()
    con.execute(
        """
        INSERT INTO run_plans (
            name, input_bucket_id, workflow_bucket_id, output_bucket_id, rules_json,
            metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            input_bucket_id=excluded.input_bucket_id,
            workflow_bucket_id=excluded.workflow_bucket_id,
            output_bucket_id=excluded.output_bucket_id,
            rules_json=excluded.rules_json,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            args.name,
            input_bucket["id"],
            workflow_bucket["id"],
            output_bucket["id"],
            json_dumps(rules),
            json_dumps(metadata),
            now,
            now,
        ),
    )
    con.commit()
    print(f"run_plan={args.name}")
    print(f"input_bucket={input_bucket['name']}")
    print(f"workflow_bucket={workflow_bucket['name']}")
    print(f"output_bucket={output_bucket['name']}")
    return 0


def cmd_run_plan_list(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    rows = con.execute(
        """
        SELECT
            rp.name,
            ib.name AS input_bucket,
            wb.name AS workflow_bucket,
            ob.name AS output_bucket,
            rp.created_at
        FROM run_plans rp
        JOIN buckets ib ON ib.id = rp.input_bucket_id
        JOIN buckets wb ON wb.id = rp.workflow_bucket_id
        JOIN buckets ob ON ob.id = rp.output_bucket_id
        ORDER BY rp.name
        """
    ).fetchall()
    for row in rows:
        print(
            f"- `{row['name']}` input=`{row['input_bucket']}` workflow=`{row['workflow_bucket']}` "
            f"output=`{row['output_bucket']}`"
        )
    return 0


def cmd_run_plan_show(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    plan = load_run_plan(con, args.name)
    print(f"# RunPlan `{plan['name']}`")
    print(f"- input_bucket: `{plan['input_bucket_name']}`")
    print(f"- workflow_bucket: `{plan['workflow_bucket_name']}`")
    print(f"- output_bucket: `{plan['output_bucket_name']}`")
    print("rules:")
    print(json.dumps(json_loads_maybe(plan["rules_json"], {}), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_run_plan_preview(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    jobs = eligible_jobs(con, args.name)
    eligible_count = 0
    print(f"# RunPlan Preview `{args.name}`")
    for job in jobs:
        asset = job["asset"]
        workflow = job["workflow"]
        marker = "eligible" if job["eligible"] else "blocked"
        if job["eligible"]:
            eligible_count += 1
        print(
            f"- {marker}: asset=`{asset['path']}` ({asset['media_type']}) "
            f"workflow=`{workflow['path']}` reason={job['reason']}"
        )
    print(f"eligible_jobs={eligible_count}")
    print(f"total_pairs={len(jobs)}")
    return 0


def create_output_asset(
    con: sqlite3.Connection,
    output_bucket_id: int,
    output_prefix: str,
    metadata: dict[str, Any],
) -> int:
    now = utc_now()
    con.execute(
        """
        INSERT INTO asset_items (bucket_id, path, media_type, role, status, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bucket_id, path) DO UPDATE SET
            status=excluded.status,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            output_bucket_id,
            output_prefix,
            "video",
            "planned_output_video",
            "planned",
            json_dumps(metadata),
            now,
            now,
        ),
    )
    row = con.execute(
        "SELECT id FROM asset_items WHERE bucket_id = ? AND path = ?",
        (output_bucket_id, output_prefix),
    ).fetchone()
    if row is None:
        raise RuntimeError("failed to create output asset item")
    return int(row["id"])


def cmd_run_plan_generate(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    plan = load_run_plan(con, args.name)
    jobs = [job for job in eligible_jobs(con, args.name) if job["eligible"]]
    limit = args.limit if args.limit is not None else len(jobs)
    workflow_dir = Path(args.workflow_dir).expanduser().resolve()
    input_root = Path(args.input_root).expanduser().resolve()
    generated = 0
    for job in jobs[:limit]:
        asset = job["asset"]
        workflow_item = job["workflow"]
        asset_path = Path(str(asset["path"]))
        workflow_path = Path(str(workflow_item["path"]))
        workflow = read_json(workflow_path)
        draft, warnings = apply_asset_to_workflow(workflow, asset_path, input_root)
        job_key = slug(f"{plan['name']}__{Path(str(asset['path'])).stem}__{workflow_stem(workflow_path)}", 120)
        output_prefix = f"{args.output_prefix_root}/{slug(plan['name'])}/{job_key}"
        changes = strip_video_previews_and_redirect_outputs(draft, output_prefix)
        output_workflow_path = workflow_dir / f"{job_key}.workflow.json"
        atomic_write_json(output_workflow_path, draft)
        metadata = {
            "plan": {
                "id": plan["id"],
                "name": plan["name"],
                "input_bucket": plan["input_bucket_name"],
                "workflow_bucket": plan["workflow_bucket_name"],
                "output_bucket": plan["output_bucket_name"],
            },
            "asset": {
                "id": asset["id"],
                "path": asset["path"],
                "media_type": asset["media_type"],
                "role": asset["role"],
            },
            "workflow": {
                "id": workflow_item["id"],
                "path": workflow_item["path"],
                "graph_hash": workflow_item["graph_hash"],
            },
            "rules": json_loads_maybe(plan["rules_json"], {}),
            "output_prefix": output_prefix,
            "generated_workflow_path": str(output_workflow_path),
            "changes": changes,
            "warnings": warnings,
        }
        output_asset_id = create_output_asset(con, plan["output_bucket_id"], output_prefix, metadata)
        now = utc_now()
        con.execute(
            """
            INSERT INTO planned_jobs (
                run_plan_id, asset_item_id, workflow_item_id, output_asset_item_id, job_key,
                status, generated_workflow_path, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_plan_id, job_key) DO UPDATE SET
                output_asset_item_id=excluded.output_asset_item_id,
                status=excluded.status,
                generated_workflow_path=excluded.generated_workflow_path,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                plan["id"],
                asset["id"],
                workflow_item["id"],
                output_asset_id,
                job_key,
                "generated",
                str(output_workflow_path),
                json_dumps(metadata),
                now,
                now,
            ),
        )
        metadata_path = Path(args.metadata_dir).expanduser().resolve() / f"{job_key}.job.json"
        atomic_write_json(metadata_path, metadata)
        generated += 1
        print(f"generated_workflow={output_workflow_path}")
        print(f"job_metadata={metadata_path}")
        print(f"output_bucket_item={output_prefix}")
        if warnings:
            for warning in warnings:
                print(f"warning={warning}")
    con.commit()
    print(f"generated_jobs={generated}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mini snowflake factory spike")
    parser.add_argument("--db", default=DEFAULT_DB, help="Factory SQLite database path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    bucket = sub.add_parser("bucket", help="Manage asset/workflow buckets")
    bucket_sub = bucket.add_subparsers(dest="bucket_cmd", required=True)

    create = bucket_sub.add_parser("create", help="Create or update a bucket")
    create.add_argument("name")
    create.add_argument("--type", choices=["asset", "workflow"], required=True)
    create.add_argument("--metadata-json")
    create.set_defaults(func=cmd_bucket_create)

    bucket_list = bucket_sub.add_parser("list", help="List buckets")
    bucket_list.set_defaults(func=cmd_bucket_list)

    show = bucket_sub.add_parser("show", help="Show bucket contents")
    show.add_argument("name")
    show.set_defaults(func=cmd_bucket_show)

    add_asset = bucket_sub.add_parser("add-asset", help="Add an asset to an asset bucket")
    add_asset.add_argument("bucket")
    add_asset.add_argument("path")
    add_asset.add_argument("--media-type", choices=["image", "video", "json", "unknown"])
    add_asset.add_argument("--role")
    add_asset.add_argument("--allow-missing", action="store_true")
    add_asset.set_defaults(func=cmd_bucket_add_asset)

    add_workflow = bucket_sub.add_parser("add-workflow", help="Add a workflow to a workflow bucket")
    add_workflow.add_argument("bucket")
    add_workflow.add_argument("path")
    add_workflow.add_argument("--workflow-type", default="review_workflow")
    add_workflow.set_defaults(func=cmd_bucket_add_workflow)

    run_plan = sub.add_parser("run-plan", help="Manage run plans")
    run_sub = run_plan.add_subparsers(dest="run_plan_cmd", required=True)

    rp_create = run_sub.add_parser("create", help="Create or update a run plan")
    rp_create.add_argument("name")
    rp_create.add_argument("--input-bucket", required=True)
    rp_create.add_argument("--workflow-bucket", required=True)
    rp_create.add_argument("--output-bucket", required=True)
    rp_create.add_argument("--rules-json")
    rp_create.set_defaults(func=cmd_run_plan_create)

    rp_list = run_sub.add_parser("list", help="List run plans")
    rp_list.set_defaults(func=cmd_run_plan_list)

    rp_show = run_sub.add_parser("show", help="Show a run plan")
    rp_show.add_argument("name")
    rp_show.set_defaults(func=cmd_run_plan_show)

    rp_preview = run_sub.add_parser("preview", help="Preview eligible jobs for a run plan")
    rp_preview.add_argument("name")
    rp_preview.set_defaults(func=cmd_run_plan_preview)

    rp_generate = run_sub.add_parser("generate", help="Generate review workflow/job artifacts for eligible jobs")
    rp_generate.add_argument("name")
    rp_generate.add_argument("--limit", type=int, default=1)
    rp_generate.add_argument("--workflow-dir", default=DEFAULT_WORKFLOW_DIR)
    rp_generate.add_argument("--metadata-dir", default="/home/yuji/src/comfyui-runpod/.data/snowflake_factory/jobs")
    rp_generate.add_argument("--input-root", default=DEFAULT_INPUT_ROOT)
    rp_generate.add_argument("--output-prefix-root", default=DEFAULT_OUTPUT_PREFIX_ROOT)
    rp_generate.set_defaults(func=cmd_run_plan_generate)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
