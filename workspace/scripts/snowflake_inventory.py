#!/usr/bin/env python3
"""
Inventory ComfyUI "snowflake" workflows embedded in artifacts.

This is an intentionally modest spike:
- scan JSON/PNG/video artifacts
- extract prompt/workflow metadata when present
- store raw extracted payloads plus simple hashes in SQLite
- print a "what exists?" report

It does not try to define the final taxonomy. The goal is to reveal the corpus.
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import hashlib
import json
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from comfy_meta_lib import (
    extract_prompt_workflow_from_png_chunks,
    extract_prompt_workflow_from_tags,
    ffprobe_format_tags,
    json_min,
    maybe_json,
    read_png_text_chunks,
    stable_json_sha256,
)


VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
ARTIFACT_EXTS = {".json", ".png"} | VIDEO_EXTS


def utc_now() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_litegraph_workflow(obj: Any) -> bool:
    return isinstance(obj, dict) and isinstance(obj.get("nodes"), list) and isinstance(obj.get("links"), list)


def is_api_prompt(obj: Any) -> bool:
    if not isinstance(obj, dict) or not obj:
        return False
    sample_values = list(obj.values())[:10]
    return any(isinstance(v, dict) and ("class_type" in v or "inputs" in v) for v in sample_values)


def png_dimensions(path: Path) -> tuple[Optional[int], Optional[int]]:
    try:
        with path.open("rb") as f:
            header = f.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            return None, None
        width, height = struct.unpack(">II", header[16:24])
        return int(width), int(height)
    except Exception:
        return None, None


def ffprobe_media_info(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"ffprobe failed for {path}")
    return json.loads(proc.stdout)


def video_dimensions_duration(path: Path) -> tuple[Optional[int], Optional[int], Optional[float]]:
    obj = ffprobe_media_info(path)
    fmt = obj.get("format") if isinstance(obj, dict) else {}
    duration = None
    if isinstance(fmt, dict):
        try:
            duration = float(fmt.get("duration")) if fmt.get("duration") is not None else None
        except (TypeError, ValueError):
            duration = None
    for stream in obj.get("streams") or []:
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            width = stream.get("width")
            height = stream.get("height")
            return (
                int(width) if isinstance(width, int) else None,
                int(height) if isinstance(height, int) else None,
                duration,
            )
    return None, None, duration


def iter_artifacts(roots: Iterable[Path], max_files: Optional[int]) -> Iterator[Path]:
    count = 0
    for root in roots:
        if root.is_file():
            candidates = [root]
        else:
            candidates = (p for p in root.rglob("*") if p.is_file())
        for path in candidates:
            if path.suffix.lower() not in ARTIFACT_EXTS:
                continue
            yield path
            count += 1
            if max_files is not None and count >= max_files:
                return


def graph_fingerprint(workflow: Any) -> Optional[str]:
    if not is_litegraph_workflow(workflow):
        return None
    nodes_by_id: dict[Any, str] = {}
    node_types: list[str] = []
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "")
        if not node_type:
            continue
        nodes_by_id[node.get("id")] = node_type
        node_types.append(node_type)

    edges: list[tuple[str, str, str]] = []
    for link in workflow.get("links") or []:
        if not isinstance(link, list) or len(link) < 6:
            continue
        src_type = nodes_by_id.get(link[1], "?")
        dst_type = nodes_by_id.get(link[3], "?")
        edge_type = str(link[5])
        edges.append((src_type, edge_type, dst_type))

    payload = {
        "node_types": sorted(collections.Counter(node_types).items()),
        "edges": sorted(edges),
    }
    return stable_json_sha256(payload)


def summarize_workflow(workflow: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "node_count": 0,
        "link_count": 0,
        "node_type_counts": {},
        "models": [],
        "loras": [],
        "generation": {},
        "optimization": {},
        "io_profile": {},
        "flags": [],
    }
    if not is_litegraph_workflow(workflow):
        return summary

    nodes = [n for n in workflow.get("nodes") or [] if isinstance(n, dict)]
    summary["node_count"] = len(nodes)
    summary["link_count"] = len(workflow.get("links") or [])
    node_types = [str(n.get("type") or n.get("class_type") or "") for n in nodes]
    node_type_counts = collections.Counter(t for t in node_types if t)
    summary["node_type_counts"] = dict(sorted(node_type_counts.items()))

    models: list[dict[str, Any]] = []
    loras: list[dict[str, Any]] = []
    generation: dict[str, Any] = {}
    optimization: dict[str, Any] = {}
    flags: list[str] = []

    for node in nodes:
        node_type = str(node.get("type") or node.get("class_type") or "")
        title = node.get("title")
        widgets = node.get("widgets_values")

        if node_type in {
            "UnetLoaderGGUFDisTorchMultiGPU",
            "UnetLoaderGGUF",
            "CLIPLoaderGGUFMultiGPU",
            "CLIPLoaderGGUF",
            "DualCLIPLoaderGGUF",
            "VAELoader",
            "CLIPVisionLoader",
            "UpscaleModelLoader",
        }:
            if isinstance(widgets, list) and widgets:
                models.append(
                    {
                        "node_id": node.get("id"),
                        "type": node_type,
                        "title": title,
                        "value": widgets[0],
                    }
                )
            if node_type == "UnetLoaderGGUFDisTorchMultiGPU" and isinstance(widgets, list):
                generation["virtual_vram_gb"] = widgets[2] if len(widgets) > 2 else None
                generation["model_device"] = widgets[1] if len(widgets) > 1 else None
                generation["use_other_vram"] = widgets[3] if len(widgets) > 3 else None

        if node_type == "Power Lora Loader (rgthree)" and isinstance(widgets, list):
            for item in widgets:
                if isinstance(item, dict) and item.get("lora"):
                    loras.append(
                        {
                            "node_id": node.get("id"),
                            "lora": item.get("lora"),
                            "on": item.get("on"),
                            "strength": item.get("strength"),
                            "strengthTwo": item.get("strengthTwo"),
                        }
                    )

        if node_type == "WanImageToVideo" and isinstance(widgets, list):
            generation["wan_widget_width"] = widgets[0] if len(widgets) > 0 else None
            generation["wan_widget_height"] = widgets[1] if len(widgets) > 1 else None
            generation["wan_widget_length"] = widgets[2] if len(widgets) > 2 else None
            generation["wan_widget_batch_size"] = widgets[3] if len(widgets) > 3 else None

        if node_type == "BasicScheduler" and isinstance(widgets, list):
            generation.setdefault("schedulers", []).append(
                {
                    "node_id": node.get("id"),
                    "scheduler": widgets[0] if len(widgets) > 0 else None,
                    "steps": widgets[1] if len(widgets) > 1 else None,
                    "denoise": widgets[2] if len(widgets) > 2 else None,
                }
            )

        if node_type == "KSamplerSelect" and isinstance(widgets, list):
            generation.setdefault("samplers", []).append(
                {"node_id": node.get("id"), "sampler": widgets[0] if widgets else None}
            )

        if node_type in {"CFGGuider", "ScheduledCFGGuidance"} and isinstance(widgets, list):
            generation.setdefault("cfg_nodes", []).append(
                {"node_id": node.get("id"), "type": node_type, "widgets": widgets}
            )

        if node_type == "WanVideoTeaCacheKJ" and isinstance(widgets, list):
            optimization["teacache"] = {
                "node_id": node.get("id"),
                "title": title,
                "mode": node.get("mode", 0),
                "enabled": node.get("mode", 0) not in (2, 4),
                "widgets": widgets,
            }

        if node_type in {
            "TorchCompileModelWanVideo",
            "CFGZeroStar",
            "CFGZeroStarAndInit",
            "ApplyRifleXRoPE_WanVideo",
            "SkipLayerGuidanceWanVideo",
        }:
            key = {
                "TorchCompileModelWanVideo": "torch_compile",
                "CFGZeroStar": "cfg_zero_star",
                "CFGZeroStarAndInit": "cfg_zero_star",
                "ApplyRifleXRoPE_WanVideo": "riflex_rope",
                "SkipLayerGuidanceWanVideo": "skip_layer_guidance",
            }[node_type]
            optimization.setdefault(key, []).append(
                {
                    "node_id": node.get("id"),
                    "type": node_type,
                    "title": title,
                    "mode": node.get("mode", 0),
                    "enabled": node.get("mode", 0) not in (2, 4),
                    "widgets": widgets,
                }
            )

        if node_type == "VHS_VideoCombine" and isinstance(widgets, dict):
            generation.setdefault("video_outputs", []).append(
                {
                    "node_id": node.get("id"),
                    "title": title,
                    "mode": node.get("mode"),
                    "frame_rate": widgets.get("frame_rate"),
                    "format": widgets.get("format"),
                    "crf": widgets.get("crf"),
                    "save_output": widgets.get("save_output"),
                }
            )

    if node_type_counts.get("VAEDecode", 0):
        flags.append("visible_vaedecode")
    if node_type_counts.get("WanImageToVideo", 0):
        flags.append("core_wan_i2v")
    if node_type_counts.get("UnetLoaderGGUFDisTorchMultiGPU", 0):
        flags.append("distorch_multigpu")
    active_video_outputs = [
        o for o in generation.get("video_outputs", []) if o.get("mode") not in (2, 4) and o.get("save_output") is not False
    ]
    if len(active_video_outputs) > 1:
        flags.append("multiple_active_video_outputs")
    postprocess_types = {
        "ImageUpscaleWithModel",
        "RIFE VFI",
        "ImageScaleBy",
        "VHS_MergeImages",
        "ColorMatch",
    }
    active_postprocess = {
        t: node_type_counts.get(t, 0)
        for t in sorted(postprocess_types)
        if node_type_counts.get(t, 0)
    }
    if any(t in active_postprocess for t in ("ImageUpscaleWithModel", "RIFE VFI")):
        flags.append("embedded_postprocess")
    if node_type_counts.get("ImageUpscaleWithModel", 0):
        flags.append("embedded_upscale")
    if node_type_counts.get("RIFE VFI", 0):
        flags.append("embedded_interpolation")

    io_profile = infer_io_profile(nodes, node_type_counts, generation)

    summary["models"] = models
    summary["loras"] = loras
    summary["generation"] = generation
    summary["optimization"] = optimization
    summary["io_profile"] = io_profile
    summary["postprocess"] = active_postprocess
    summary["flags"] = sorted(set(flags))
    return summary


def infer_io_profile(
    nodes: list[dict[str, Any]],
    node_type_counts: collections.Counter[str],
    generation: dict[str, Any],
) -> dict[str, Any]:
    image_input_nodes = {"LoadImage", "LoadImageWithFilename|pysssss", "LoadImageOutput", "VHS_LoadImagePath"}
    video_input_nodes = {"VHS_LoadVideo", "VHS_LoadVideoPath", "VHS_LoadVideoFFmpeg", "VHS_LoadVideoFFmpegPath"}
    sequence_input_nodes = {"VHS_LoadImages", "VHS_LoadImagesPath", "LoadImagesFromFolderKJ", "LayerUtility: LoadImagesFromPath"}
    internal_sequence_nodes = {"VHS_MergeImages", "ImageListToImageBatch", "ImageBatchToImageList"}

    inputs: set[str] = set()
    outputs: set[str] = set()
    internal: set[str] = set()
    debug_outputs: list[dict[str, Any]] = []
    sample_frame_outputs: list[dict[str, Any]] = []

    if any(node_type_counts.get(t, 0) for t in image_input_nodes):
        inputs.add("image")
    if any(node_type_counts.get(t, 0) for t in video_input_nodes):
        inputs.add("video")
    if any(node_type_counts.get(t, 0) for t in sequence_input_nodes):
        inputs.add("image_sequence")
    if any(node_type_counts.get(t, 0) for t in internal_sequence_nodes):
        internal.add("image_sequence")

    for output in generation.get("video_outputs") or []:
        if output.get("mode") in (2, 4) or output.get("save_output") is False:
            continue
        title = str(output.get("title") or "")
        outputs.add("video")
        if "preview" in title.lower() or "raw" in title.lower():
            outputs.add("debug_preview")
            debug_outputs.append(output)

    for node in nodes:
        node_type = str(node.get("type") or node.get("class_type") or "")
        if node_type != "SaveImage":
            continue
        if node.get("mode", 0) in (2, 4):
            continue
        outputs.add("image")
        title = str(node.get("title") or "")
        widgets = node.get("widgets_values")
        prefix = widgets[0] if isinstance(widgets, list) and widgets else ""
        if "last frame" in title.lower() or "_LF" in str(prefix):
            outputs.add("sample_frame")
            sample_frame_outputs.append(
                {
                    "node_id": node.get("id"),
                    "title": title,
                    "prefix": prefix,
                }
            )

    if not inputs:
        inputs.add("unknown")
    if not outputs:
        outputs.add("unknown")

    return {
        "inputs": sorted(inputs),
        "outputs": sorted(outputs),
        "internal": sorted(internal),
        "debug_outputs": debug_outputs,
        "sample_frame_outputs": sample_frame_outputs,
    }


def prompt_node_type_counts(prompt: Any) -> dict[str, int]:
    if not is_api_prompt(prompt):
        return {}
    counts: collections.Counter[str] = collections.Counter()
    for node in prompt.values():
        if isinstance(node, dict) and isinstance(node.get("class_type"), str):
            counts[node["class_type"]] += 1
    return dict(sorted(counts.items()))


def normalize_model_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return value.replace("\\", "/").strip()


def preset_payloads(workflow_summary: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "models": workflow_summary.get("models") or [],
        "loras": workflow_summary.get("loras") or [],
        "generation": workflow_summary.get("generation") or {},
    }

    model_payload = []
    for model in fields["models"]:
        if not isinstance(model, dict):
            continue
        model_payload.append(
            {
                "type": model.get("type"),
                "title": model.get("title"),
                "value": normalize_model_value(model.get("value")),
            }
        )

    lora_payload = []
    for lora in fields["loras"]:
        if not isinstance(lora, dict) or lora.get("on") is not True:
            continue
        lora_payload.append(
            {
                "lora": normalize_model_value(lora.get("lora")),
                "strength": lora.get("strength"),
                "strengthTwo": lora.get("strengthTwo"),
            }
        )

    generation = dict(fields["generation"])
    output_payload = {
        "video_outputs": generation.pop("video_outputs", []),
    }
    optimization_payload = workflow_summary.get("optimization") or {}
    postprocess_payload = workflow_summary.get("postprocess") or {}
    generation_payload = generation

    return {
        "model": sorted(model_payload, key=lambda x: (str(x.get("type")), str(x.get("title")), str(x.get("value")))),
        "lora": sorted(lora_payload, key=lambda x: (str(x.get("lora")), str(x.get("strength")), str(x.get("strengthTwo")))),
        "generation": generation_payload,
        "optimization": optimization_payload,
        "output": output_payload,
        "postprocess": postprocess_payload,
    }


def recipe_fingerprint(workflow_summary: dict[str, Any]) -> Optional[str]:
    payloads = preset_payloads(workflow_summary)
    payload = {
        "model_preset_hash": stable_json_sha256(payloads["model"]),
        "lora_preset_hash": stable_json_sha256(payloads["lora"]),
        "generation_config_hash": stable_json_sha256(payloads["generation"]),
        "optimization_preset_hash": stable_json_sha256(payloads["optimization"]),
        "output_preset_hash": stable_json_sha256(payloads["output"]),
        "postprocess_preset_hash": stable_json_sha256(payloads["postprocess"]),
    }
    return stable_json_sha256(payload)


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE NOT NULL,
            ext TEXT NOT NULL,
            kind TEXT NOT NULL,
            size_bytes INTEGER,
            mtime REAL,
            status TEXT NOT NULL,
            error TEXT,
            media_width INTEGER,
            media_height INTEGER,
            media_duration REAL,
            metadata_keys_json TEXT,
            scanned_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_snapshots (
            id INTEGER PRIMARY KEY,
            artifact_id INTEGER NOT NULL,
            source_kind TEXT NOT NULL,
            workflow_hash TEXT,
            prompt_hash TEXT,
            graph_hash TEXT,
            recipe_hash TEXT,
            model_preset_hash TEXT,
            lora_preset_hash TEXT,
            generation_config_hash TEXT,
            optimization_preset_hash TEXT,
            output_preset_hash TEXT,
            postprocess_preset_hash TEXT,
            io_profile_json TEXT,
            node_count INTEGER,
            link_count INTEGER,
            node_type_counts_json TEXT,
            prompt_node_type_counts_json TEXT,
            key_fields_json TEXT,
            workflow_json TEXT,
            prompt_json TEXT,
            FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_ext ON artifacts(ext)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_workflow_hash ON workflow_snapshots(workflow_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_graph_hash ON workflow_snapshots(graph_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_recipe_hash ON workflow_snapshots(recipe_hash)")
    ensure_column(con, "workflow_snapshots", "model_preset_hash", "TEXT")
    ensure_column(con, "workflow_snapshots", "lora_preset_hash", "TEXT")
    ensure_column(con, "workflow_snapshots", "generation_config_hash", "TEXT")
    ensure_column(con, "workflow_snapshots", "optimization_preset_hash", "TEXT")
    ensure_column(con, "workflow_snapshots", "output_preset_hash", "TEXT")
    ensure_column(con, "workflow_snapshots", "postprocess_preset_hash", "TEXT")
    ensure_column(con, "workflow_snapshots", "io_profile_json", "TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_model_preset_hash ON workflow_snapshots(model_preset_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_lora_preset_hash ON workflow_snapshots(lora_preset_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_generation_config_hash ON workflow_snapshots(generation_config_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_optimization_preset_hash ON workflow_snapshots(optimization_preset_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_output_preset_hash ON workflow_snapshots(output_preset_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_postprocess_preset_hash ON workflow_snapshots(postprocess_preset_hash)")
    return con


def ensure_column(con: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {str(row[1]) for row in rows}
    if column not in existing:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def reset_db(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM workflow_snapshots")
    con.execute("DELETE FROM artifacts")
    con.commit()


def upsert_artifact(
    con: sqlite3.Connection,
    path: Path,
    status: str,
    error: Optional[str],
    media_width: Optional[int],
    media_height: Optional[int],
    media_duration: Optional[float],
    metadata_keys: list[str],
) -> int:
    stat = path.stat()
    con.execute(
        """
        INSERT INTO artifacts (
            path, ext, kind, size_bytes, mtime, status, error, media_width, media_height,
            media_duration, metadata_keys_json, scanned_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            ext=excluded.ext,
            kind=excluded.kind,
            size_bytes=excluded.size_bytes,
            mtime=excluded.mtime,
            status=excluded.status,
            error=excluded.error,
            media_width=excluded.media_width,
            media_height=excluded.media_height,
            media_duration=excluded.media_duration,
            metadata_keys_json=excluded.metadata_keys_json,
            scanned_at=excluded.scanned_at
        """,
        (
            str(path),
            path.suffix.lower(),
            artifact_kind(path),
            stat.st_size,
            stat.st_mtime,
            status,
            error,
            media_width,
            media_height,
            media_duration,
            json.dumps(metadata_keys, ensure_ascii=False),
            utc_now(),
        ),
    )
    row = con.execute("SELECT id FROM artifacts WHERE path = ?", (str(path),)).fetchone()
    if row is None:
        raise RuntimeError(f"failed to upsert artifact: {path}")
    artifact_id = int(row[0])
    con.execute("DELETE FROM workflow_snapshots WHERE artifact_id = ?", (artifact_id,))
    return artifact_id


def artifact_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".png":
        return "png"
    if ext in VIDEO_EXTS:
        return "video"
    return "unknown"


def insert_snapshot(
    con: sqlite3.Connection,
    artifact_id: int,
    source_kind: str,
    workflow: Any,
    prompt: Any,
    store_payloads: bool,
) -> None:
    workflow_summary = summarize_workflow(workflow)
    workflow_hash = stable_json_sha256(workflow) if workflow is not None else None
    prompt_hash = stable_json_sha256(prompt) if prompt is not None else None
    graph_hash = graph_fingerprint(workflow)
    preset_hashes = preset_payloads(workflow_summary)
    model_preset_hash = stable_json_sha256(preset_hashes["model"])
    lora_preset_hash = stable_json_sha256(preset_hashes["lora"])
    generation_config_hash = stable_json_sha256(preset_hashes["generation"])
    optimization_preset_hash = stable_json_sha256(preset_hashes["optimization"])
    output_preset_hash = stable_json_sha256(preset_hashes["output"])
    postprocess_preset_hash = stable_json_sha256(preset_hashes["postprocess"])
    rec_hash = recipe_fingerprint(workflow_summary)
    con.execute(
        """
        INSERT INTO workflow_snapshots (
            artifact_id, source_kind, workflow_hash, prompt_hash, graph_hash, recipe_hash,
            model_preset_hash, lora_preset_hash, generation_config_hash, optimization_preset_hash,
            output_preset_hash, postprocess_preset_hash, io_profile_json,
            node_count, link_count, node_type_counts_json, prompt_node_type_counts_json,
            key_fields_json, workflow_json, prompt_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            source_kind,
            workflow_hash,
            prompt_hash,
            graph_hash,
            rec_hash,
            model_preset_hash,
            lora_preset_hash,
            generation_config_hash,
            optimization_preset_hash,
            output_preset_hash,
            postprocess_preset_hash,
            json.dumps(workflow_summary.get("io_profile") or {}, ensure_ascii=False, sort_keys=True),
            workflow_summary.get("node_count"),
            workflow_summary.get("link_count"),
            json.dumps(workflow_summary.get("node_type_counts") or {}, ensure_ascii=False, sort_keys=True),
            json.dumps(prompt_node_type_counts(prompt), ensure_ascii=False, sort_keys=True),
            json.dumps(
                {
                    "models": workflow_summary.get("models") or [],
                    "loras": workflow_summary.get("loras") or [],
                    "generation": workflow_summary.get("generation") or {},
                    "optimization": workflow_summary.get("optimization") or {},
                    "io_profile": workflow_summary.get("io_profile") or {},
                    "postprocess": workflow_summary.get("postprocess") or {},
                    "flags": workflow_summary.get("flags") or [],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            json_min(workflow) if store_payloads and workflow is not None else None,
            json_min(prompt) if store_payloads and prompt is not None else None,
        ),
    )


def extract_from_json(path: Path) -> tuple[str, Optional[Any], Optional[Any], list[str], Optional[str]]:
    try:
        obj = read_json(path)
    except Exception as exc:
        return "parse_error", None, None, [], str(exc)
    if is_litegraph_workflow(obj):
        return "ok", obj, None, sorted(obj.keys()), None
    if is_api_prompt(obj):
        return "ok", None, obj, sorted(obj.keys())[:50], None
    if isinstance(obj, dict) and ("workflow" in obj or "prompt" in obj):
        workflow = obj.get("workflow") if is_litegraph_workflow(obj.get("workflow")) else maybe_json(obj.get("workflow"))
        prompt = obj.get("prompt") if is_api_prompt(obj.get("prompt")) else maybe_json(obj.get("prompt"))
        if workflow is not None or prompt is not None:
            return "ok", workflow, prompt, sorted(obj.keys()), None
    return "no_workflow", None, None, sorted(obj.keys()) if isinstance(obj, dict) else [], None


def scan_one(con: sqlite3.Connection, path: Path, store_payloads: bool) -> None:
    ext = path.suffix.lower()
    status = "no_workflow"
    error = None
    workflow = None
    prompt = None
    snapshot_source_kind = artifact_kind(path)
    metadata_keys: list[str] = []
    media_width = None
    media_height = None
    media_duration = None

    try:
        if ext == ".json":
            status, workflow, prompt, metadata_keys, error = extract_from_json(path)
        elif ext == ".png":
            media_width, media_height = png_dimensions(path)
            chunks = read_png_text_chunks(path)
            metadata_keys = sorted(chunks.keys())
            prompt, workflow = extract_prompt_workflow_from_png_chunks(chunks)
            status = "ok" if workflow is not None or prompt is not None else "no_workflow"
        elif ext in VIDEO_EXTS:
            tags: dict[str, Any] = {}
            try:
                media_width, media_height, media_duration = video_dimensions_duration(path)
                tags = ffprobe_format_tags(path)
            except Exception as exc:
                error = f"ffprobe_media_info: {exc}"
            if tags:
                metadata_keys = sorted(tags.keys())
                prompt, workflow = extract_prompt_workflow_from_tags(tags)

            # Host machines may not have ffprobe installed, and many ComfyUI videos
            # have same-stem PNG sidecars containing the exact prompt/workflow.
            if workflow is None and prompt is None:
                companion_png = path.with_suffix(".png")
                if companion_png.exists():
                    chunks = read_png_text_chunks(companion_png)
                    metadata_keys = [f"companion_png:{k}" for k in sorted(chunks.keys())]
                    prompt, workflow = extract_prompt_workflow_from_png_chunks(chunks)
                    if media_width is None or media_height is None:
                        media_width, media_height = png_dimensions(companion_png)
                    snapshot_source_kind = "video_companion_png"
            status = "ok" if workflow is not None or prompt is not None else "no_workflow"
        else:
            status = "unsupported"
    except Exception as exc:
        status = "extract_error"
        error = str(exc)

    artifact_id = upsert_artifact(
        con,
        path,
        status,
        error,
        media_width,
        media_height,
        media_duration,
        metadata_keys,
    )
    if workflow is not None or prompt is not None:
        insert_snapshot(con, artifact_id, snapshot_source_kind, workflow, prompt, store_payloads)


def extract_snapshot_payload_from_artifact(path: Path) -> tuple[str, Any, Any]:
    ext = path.suffix.lower()
    source_kind = artifact_kind(path)
    workflow = None
    prompt = None
    error = None

    if ext == ".json":
        status, workflow, prompt, _metadata_keys, error = extract_from_json(path)
        if status not in {"ok", "no_workflow"}:
            raise RuntimeError(error or f"failed to extract JSON metadata from {path}")
    elif ext == ".png":
        chunks = read_png_text_chunks(path)
        prompt, workflow = extract_prompt_workflow_from_png_chunks(chunks)
    elif ext in VIDEO_EXTS:
        try:
            tags = ffprobe_format_tags(path)
            prompt, workflow = extract_prompt_workflow_from_tags(tags)
        except Exception as exc:
            error = f"ffprobe_format_tags: {exc}"

        if workflow is None and prompt is None:
            companion_png = path.with_suffix(".png")
            if companion_png.exists():
                chunks = read_png_text_chunks(companion_png)
                prompt, workflow = extract_prompt_workflow_from_png_chunks(chunks)
                source_kind = "video_companion_png"
        if workflow is None and prompt is None and error:
            raise RuntimeError(error)
    else:
        raise RuntimeError(f"unsupported artifact type: {path.suffix}")

    return source_kind, workflow, prompt


def scan(args: argparse.Namespace) -> int:
    roots = [Path(p).expanduser().resolve() for p in args.paths]
    con = open_db(Path(args.db))
    if args.reset:
        reset_db(con)

    scanned = 0
    for path in iter_artifacts(roots, args.max_files):
        scan_one(con, path, store_payloads=not args.no_store_payloads)
        scanned += 1
        if args.progress and scanned % args.progress == 0:
            print(f"scanned {scanned} artifacts...", file=sys.stderr)
    con.commit()
    print(f"scanned_artifacts={scanned}")
    print(f"db={args.db}")
    return 0


def _rows(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    return list(con.execute(sql, params))


def report(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    con.row_factory = sqlite3.Row

    print(f"# Snowflake Inventory Report\n")
    print(f"Database: `{args.db}`\n")

    total = con.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    snapshots = con.execute("SELECT COUNT(*) FROM workflow_snapshots").fetchone()[0]
    print(f"- Artifacts scanned: {total}")
    print(f"- Extracted workflow/prompt snapshots: {snapshots}\n")

    print("## Distinct Identities")
    identity_columns = [
        ("workflow_hash", "raw workflow"),
        ("graph_hash", "graph"),
        ("recipe_hash", "recipe excluding prompt"),
        ("model_preset_hash", "model preset"),
        ("lora_preset_hash", "LoRA preset"),
        ("generation_config_hash", "generation config"),
        ("optimization_preset_hash", "optimization preset"),
        ("output_preset_hash", "output preset"),
        ("postprocess_preset_hash", "postprocess preset"),
        ("prompt_hash", "prompt"),
    ]
    for column, label in identity_columns:
        count = con.execute(
            f"SELECT COUNT(DISTINCT {column}) FROM workflow_snapshots WHERE {column} IS NOT NULL"
        ).fetchone()[0]
        print(f"- {label}: {count}")
    print()

    print("## Snapshot Sources")
    for row in _rows(
        con,
        """
        SELECT source_kind, COUNT(*) AS count
        FROM workflow_snapshots
        GROUP BY source_kind
        ORDER BY count DESC, source_kind
        """,
    ):
        print(f"- `{row['source_kind']}`: {row['count']}")
    print()

    print("## Artifacts By Type And Status")
    for row in _rows(
        con,
        """
        SELECT ext, status, COUNT(*) AS count
        FROM artifacts
        GROUP BY ext, status
        ORDER BY count DESC, ext, status
        """,
    ):
        print(f"- `{row['ext']}` `{row['status']}`: {row['count']}")

    print("\n## Metadata Keys")
    key_counts: collections.Counter[str] = collections.Counter()
    for row in _rows(con, "SELECT metadata_keys_json FROM artifacts WHERE metadata_keys_json IS NOT NULL"):
        try:
            for key in json.loads(row["metadata_keys_json"]):
                key_counts[str(key)] += 1
        except Exception:
            continue
    for key, count in key_counts.most_common(args.limit):
        print(f"- `{key}`: {count}")

    print("\n## Top Workflow Graph Hashes")
    for row in _rows(
        con,
        """
        SELECT graph_hash, COUNT(*) AS count, MAX(node_count) AS nodes, MIN(a.path) AS example_path
        FROM workflow_snapshots s
        JOIN artifacts a ON a.id = s.artifact_id
        WHERE graph_hash IS NOT NULL
        GROUP BY graph_hash
        ORDER BY count DESC
        LIMIT ?
        """,
        (args.limit,),
    ):
        print(f"- `{row['graph_hash']}`: {row['count']} artifacts, nodes={row['nodes']}, example=`{row['example_path']}`")

    print("\n## Recipes Per Graph")
    for row in _rows(
        con,
        """
        SELECT
            graph_hash,
            COUNT(*) AS artifacts,
            COUNT(DISTINCT recipe_hash) AS recipes,
            COUNT(DISTINCT prompt_hash) AS prompts,
            MIN(a.path) AS example_path
        FROM workflow_snapshots s
        JOIN artifacts a ON a.id = s.artifact_id
        WHERE graph_hash IS NOT NULL
        GROUP BY graph_hash
        ORDER BY artifacts DESC
        LIMIT ?
        """,
        (args.limit,),
    ):
        print(
            f"- `{row['graph_hash']}`: artifacts={row['artifacts']}, "
            f"recipes_without_prompt={row['recipes']}, prompts={row['prompts']}, "
            f"example=`{row['example_path']}`"
        )

    preset_sections = [
        ("model_preset_hash", "Top Model Presets"),
        ("lora_preset_hash", "Top LoRA Presets"),
        ("generation_config_hash", "Top Generation Configs"),
        ("optimization_preset_hash", "Top Optimization Presets"),
        ("output_preset_hash", "Top Output Presets"),
        ("postprocess_preset_hash", "Top Postprocess Presets"),
    ]
    for column, title in preset_sections:
        print(f"\n## {title}")
        for row in _rows(
            con,
            f"""
            SELECT {column} AS hash, COUNT(*) AS count, MIN(a.path) AS example_path
            FROM workflow_snapshots s
            JOIN artifacts a ON a.id = s.artifact_id
            WHERE {column} IS NOT NULL
            GROUP BY {column}
            ORDER BY count DESC
            LIMIT ?
            """,
            (args.limit,),
        ):
            print(f"- `{row['hash']}`: {row['count']} artifacts, example=`{row['example_path']}`")

    print("\n## Top Node Types")
    node_counts: collections.Counter[str] = collections.Counter()
    for row in _rows(con, "SELECT node_type_counts_json FROM workflow_snapshots"):
        try:
            node_counts.update(json.loads(row["node_type_counts_json"] or "{}"))
        except Exception:
            continue
    for node_type, count in node_counts.most_common(args.limit):
        print(f"- `{node_type}`: {count}")

    print("\n## Workflow I/O Profiles")
    io_counts: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for row in _rows(con, "SELECT io_profile_json FROM workflow_snapshots"):
        io_profile = _json_loads_maybe(row["io_profile_json"], {})
        inputs = "+".join(io_profile.get("inputs") or ["unknown"])
        outputs = "+".join(io_profile.get("outputs") or ["unknown"])
        internal = "+".join(io_profile.get("internal") or [])
        io_counts[(inputs, outputs, internal)] += 1
    for (inputs, outputs, internal), count in io_counts.most_common(args.limit):
        internal_part = f", internal={internal}" if internal else ""
        print(f"- `{inputs}` -> `{outputs}`{internal_part}: {count}")

    print("\n## Top Models")
    model_counts: collections.Counter[str] = collections.Counter()
    lora_counts: collections.Counter[str] = collections.Counter()
    flag_counts: collections.Counter[str] = collections.Counter()
    for row in _rows(con, "SELECT key_fields_json FROM workflow_snapshots"):
        try:
            fields = json.loads(row["key_fields_json"] or "{}")
        except Exception:
            continue
        for model in fields.get("models") or []:
            if isinstance(model, dict) and model.get("value"):
                model_counts[str(model["value"])] += 1
        for lora in fields.get("loras") or []:
            if isinstance(lora, dict) and lora.get("lora"):
                suffix = " (on)" if lora.get("on") is True else " (off)" if lora.get("on") is False else ""
                lora_counts[str(lora["lora"]) + suffix] += 1
        for flag in fields.get("flags") or []:
            flag_counts[str(flag)] += 1
    for model, count in model_counts.most_common(args.limit):
        print(f"- `{model}`: {count}")

    print("\n## Top LoRAs")
    for lora, count in lora_counts.most_common(args.limit):
        print(f"- `{lora}`: {count}")

    print("\n## Optimization Flags")
    for flag, count in flag_counts.most_common(args.limit):
        print(f"- `{flag}`: {count}")

    print("\n## Recent Extract Errors")
    for row in _rows(
        con,
        """
        SELECT path, status, error
        FROM artifacts
        WHERE status IN ('parse_error', 'extract_error')
        ORDER BY mtime DESC
        LIMIT ?
        """,
        (args.limit,),
    ):
        print(f"- `{row['status']}` `{row['path']}`: {row['error']}")

    print("\n## Recent Extract Warnings")
    for row in _rows(
        con,
        """
        SELECT path, status, error
        FROM artifacts
        WHERE status NOT IN ('parse_error', 'extract_error') AND error IS NOT NULL
        ORDER BY mtime DESC
        LIMIT ?
        """,
        (args.limit,),
    ):
        print(f"- `{row['status']}` `{row['path']}`: {row['error']}")

    return 0


HASH_COLUMNS = {
    "workflow": "workflow_hash",
    "prompt": "prompt_hash",
    "graph": "graph_hash",
    "recipe": "recipe_hash",
    "model": "model_preset_hash",
    "lora": "lora_preset_hash",
    "generation": "generation_config_hash",
    "optimization": "optimization_preset_hash",
    "output": "output_preset_hash",
    "postprocess": "postprocess_preset_hash",
}


def _json_loads_maybe(value: Any, default: Any) -> Any:
    if not isinstance(value, str) or not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _print_json_block(label: str, value: Any) -> None:
    print(f"{label}:")
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def describe_fields(fields: dict[str, Any], kind: str) -> str:
    if kind == "model":
        models = [m for m in fields.get("models") or [] if isinstance(m, dict)]
        priority = [
            "UnetLoaderGGUFDisTorchMultiGPU",
            "UnetLoaderGGUF",
            "CLIPLoaderGGUFMultiGPU",
            "CLIPLoaderGGUF",
            "DualCLIPLoaderGGUF",
            "VAELoader",
        ]
        ordered = sorted(
            models,
            key=lambda m: priority.index(str(m.get("type"))) if str(m.get("type")) in priority else len(priority),
        )
        values = [normalize_model_value(m.get("value")) for m in ordered if m.get("value")]
        return "no_model_values" if not values else ", ".join(str(v).split("/")[-1] for v in values[:3])
    if kind == "lora":
        active = [
            f"{l.get('lora')}@{l.get('strength')}"
            for l in fields.get("loras") or []
            if isinstance(l, dict) and l.get("on") is True
        ]
        return "no_active_loras" if not active else ", ".join(active[:4])
    if kind == "generation":
        gen = fields.get("generation") or {}
        sched = (gen.get("schedulers") or [{}])[0]
        sampler = (gen.get("samplers") or [{}])[0]
        return (
            f"{sampler.get('sampler', 'sampler?')} "
            f"{sched.get('scheduler', 'sched?')} steps={sched.get('steps')} denoise={sched.get('denoise')} "
            f"vvram={gen.get('virtual_vram_gb')}"
        )
    if kind == "optimization":
        opt = fields.get("optimization") or {}
        parts = []
        teacache = opt.get("teacache")
        if isinstance(teacache, dict):
            widgets = teacache.get("widgets") or []
            parts.append(f"TeaCache={'on' if teacache.get('enabled') else 'off'} {widgets}")
        for key in ("torch_compile", "cfg_zero_star", "riflex_rope", "skip_layer_guidance"):
            values = opt.get(key) or []
            if isinstance(values, list) and values:
                enabled = any(v.get("enabled") for v in values if isinstance(v, dict))
                parts.append(f"{key}={'on' if enabled else 'off'}")
        return ", ".join(parts) if parts else "no_optimization_nodes"
    if kind == "output":
        outputs = (fields.get("generation") or {}).get("video_outputs") or []
        active = [o for o in outputs if o.get("mode") not in (2, 4) and o.get("save_output") is not False]
        return ", ".join(f"{o.get('title')}@{o.get('frame_rate')}fps" for o in active[:4]) or "no_active_video_outputs"
    if kind == "postprocess":
        post = fields.get("postprocess") or {}
        return "none" if not post else ", ".join(f"{k}x{v}" for k, v in sorted(post.items()))
    return ""


def mainline(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    con.row_factory = sqlite3.Row
    min_artifacts = args.min_artifacts

    print("# Snowflake Mainline Report\n")
    print(f"Database: `{args.db}`")
    print(f"Mainline threshold: graph families with at least {min_artifacts} artifacts\n")

    total_snapshots = con.execute("SELECT COUNT(*) FROM workflow_snapshots").fetchone()[0]
    mainline_snapshots = con.execute(
        """
        WITH mainline_graphs AS (
            SELECT graph_hash
            FROM workflow_snapshots
            WHERE graph_hash IS NOT NULL
            GROUP BY graph_hash
            HAVING COUNT(*) >= ?
        )
        SELECT COUNT(*)
        FROM workflow_snapshots s
        JOIN mainline_graphs g ON g.graph_hash = s.graph_hash
        """,
        (min_artifacts,),
    ).fetchone()[0]
    pct = (mainline_snapshots / total_snapshots * 100.0) if total_snapshots else 0.0
    print(f"- Mainline snapshots: {mainline_snapshots} / {total_snapshots} ({pct:.1f}%)\n")

    print("## Mainline Graph Families")
    for row in _rows(
        con,
        """
        SELECT
            graph_hash,
            COUNT(*) AS artifacts,
            MAX(node_count) AS nodes,
            COUNT(DISTINCT recipe_hash) AS recipes,
            COUNT(DISTINCT prompt_hash) AS prompts,
            MIN(a.path) AS example_path
        FROM workflow_snapshots s
        JOIN artifacts a ON a.id = s.artifact_id
        WHERE graph_hash IS NOT NULL
        GROUP BY graph_hash
        HAVING artifacts >= ?
        ORDER BY artifacts DESC
        LIMIT ?
        """,
        (min_artifacts, args.limit),
    ):
        print(
            f"- artifacts={row['artifacts']} nodes={row['nodes']} recipes={row['recipes']} "
            f"prompts={row['prompts']} graph=`{row['graph_hash']}` example=`{row['example_path']}`"
        )

    print("\n## Mainline Preset Layers")
    for kind, column in [
        ("model", "model_preset_hash"),
        ("lora", "lora_preset_hash"),
        ("generation", "generation_config_hash"),
        ("optimization", "optimization_preset_hash"),
        ("output", "output_preset_hash"),
        ("postprocess", "postprocess_preset_hash"),
    ]:
        print(f"\n### {kind}")
        for row in _rows(
            con,
            f"""
            WITH mainline_graphs AS (
                SELECT graph_hash
                FROM workflow_snapshots
                WHERE graph_hash IS NOT NULL
                GROUP BY graph_hash
                HAVING COUNT(*) >= ?
            )
            SELECT
                s.{column} AS hash,
                COUNT(*) AS artifacts,
                MIN(a.path) AS example_path,
                MIN(s.key_fields_json) AS fields
            FROM workflow_snapshots s
            JOIN artifacts a ON a.id = s.artifact_id
            JOIN mainline_graphs g ON g.graph_hash = s.graph_hash
            WHERE s.{column} IS NOT NULL
            GROUP BY s.{column}
            ORDER BY artifacts DESC
            LIMIT ?
            """,
            (min_artifacts, args.limit),
        ):
            fields = _json_loads_maybe(row["fields"], {})
            label = describe_fields(fields, kind)
            print(f"- artifacts={row['artifacts']} `{row['hash']}` {label} example=`{row['example_path']}`")

    print("\n## Mainline I/O Profiles")
    io_counts: collections.Counter[tuple[str, str, str]] = collections.Counter()
    for row in _rows(
        con,
        """
        WITH mainline_graphs AS (
            SELECT graph_hash
            FROM workflow_snapshots
            WHERE graph_hash IS NOT NULL
            GROUP BY graph_hash
            HAVING COUNT(*) >= ?
        )
        SELECT s.io_profile_json
        FROM workflow_snapshots s
        JOIN mainline_graphs g ON g.graph_hash = s.graph_hash
        """,
        (min_artifacts,),
    ):
        io_profile = _json_loads_maybe(row["io_profile_json"], {})
        inputs = "+".join(io_profile.get("inputs") or ["unknown"])
        outputs = "+".join(io_profile.get("outputs") or ["unknown"])
        internal = "+".join(io_profile.get("internal") or [])
        io_counts[(inputs, outputs, internal)] += 1
    for (inputs, outputs, internal), count in io_counts.most_common(args.limit):
        internal_part = f", internal={internal}" if internal else ""
        print(f"- `{inputs}` -> `{outputs}`{internal_part}: {count}")

    return 0


PRESET_SECTIONS = [
    ("model", "model_preset_hash"),
    ("lora", "lora_preset_hash"),
    ("generation", "generation_config_hash"),
    ("optimization", "optimization_preset_hash"),
    ("output", "output_preset_hash"),
    ("postprocess", "postprocess_preset_hash"),
]


def workflow_node_manifest(workflow: Any) -> list[dict[str, Any]]:
    if not is_litegraph_workflow(workflow):
        return []
    manifest = []
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "")
        manifest.append(
            {
                "id": node.get("id"),
                "type": node_type,
                "title": node.get("title"),
                "mode": node.get("mode", 0),
            }
        )
    return sorted(manifest, key=lambda n: (str(n.get("type")), str(n.get("id"))))


def graph_family_stats(con: sqlite3.Connection, graph_hash: str) -> dict[str, Any]:
    row = con.execute(
        """
        SELECT
            COUNT(*) AS artifacts,
            COUNT(DISTINCT recipe_hash) AS recipes,
            COUNT(DISTINCT prompt_hash) AS prompts,
            COUNT(DISTINCT model_preset_hash) AS model_presets,
            COUNT(DISTINCT lora_preset_hash) AS lora_presets,
            COUNT(DISTINCT generation_config_hash) AS generation_configs,
            COUNT(DISTINCT optimization_preset_hash) AS optimization_presets,
            COUNT(DISTINCT output_preset_hash) AS output_presets,
            COUNT(DISTINCT postprocess_preset_hash) AS postprocess_presets,
            MAX(node_count) AS node_count,
            MAX(link_count) AS link_count
        FROM workflow_snapshots
        WHERE graph_hash = ?
        """,
        (graph_hash,),
    ).fetchone()
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def top_graph_hash(con: sqlite3.Connection) -> Optional[str]:
    row = con.execute(
        """
        SELECT graph_hash
        FROM workflow_snapshots
        WHERE graph_hash IS NOT NULL
        GROUP BY graph_hash
        ORDER BY COUNT(*) DESC, graph_hash
        LIMIT 1
        """
    ).fetchone()
    return str(row["graph_hash"]) if row is not None else None


def representative_snapshot_row(con: sqlite3.Connection, graph_hash: str) -> Optional[sqlite3.Row]:
    return con.execute(
        """
        SELECT
            a.path AS artifact_path,
            a.ext AS artifact_ext,
            a.kind AS artifact_kind,
            s.source_kind,
            s.workflow_hash,
            s.prompt_hash,
            s.graph_hash,
            s.recipe_hash,
            s.model_preset_hash,
            s.lora_preset_hash,
            s.generation_config_hash,
            s.optimization_preset_hash,
            s.output_preset_hash,
            s.postprocess_preset_hash,
            s.node_count,
            s.link_count,
            s.node_type_counts_json,
            s.prompt_node_type_counts_json,
            s.key_fields_json,
            s.io_profile_json
        FROM workflow_snapshots s
        JOIN artifacts a ON a.id = s.artifact_id
        WHERE s.graph_hash = ?
        ORDER BY
            CASE a.ext WHEN '.json' THEN 0 WHEN '.png' THEN 1 ELSE 2 END,
            a.path
        LIMIT 1
        """,
        (graph_hash,),
    ).fetchone()


def preset_variants_for_graph(
    con: sqlite3.Connection,
    graph_hash: str,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    variants: dict[str, list[dict[str, Any]]] = {}
    for kind, column in PRESET_SECTIONS:
        rows = _rows(
            con,
            f"""
            SELECT
                s.{column} AS hash,
                COUNT(*) AS artifacts,
                MIN(a.path) AS example_path,
                MIN(s.key_fields_json) AS fields
            FROM workflow_snapshots s
            JOIN artifacts a ON a.id = s.artifact_id
            WHERE s.graph_hash = ? AND s.{column} IS NOT NULL
            GROUP BY s.{column}
            ORDER BY artifacts DESC, hash
            LIMIT ?
            """,
            (graph_hash, limit),
        )
        variants[kind] = [
            {
                "hash": row["hash"],
                "artifacts": row["artifacts"],
                "label": describe_fields(_json_loads_maybe(row["fields"], {}), kind),
                "example_path": row["example_path"],
            }
            for row in rows
        ]
    return variants


def separation_recommendations(fields: dict[str, Any]) -> list[str]:
    recommendations = []
    flags = set(str(f) for f in fields.get("flags") or [])
    io_profile = fields.get("io_profile") or {}
    if "embedded_postprocess" in flags:
        recommendations.append("Move upscale/interpolation/color-match nodes into managed postprocess workflows.")
    if "multiple_active_video_outputs" in flags:
        recommendations.append("Keep one final video output in the template; treat previews/debug outputs as orchestrator-managed outputs.")
    outputs = set(str(o) for o in io_profile.get("outputs") or [])
    if "sample_frame" in outputs:
        recommendations.append("Represent sample-frame extraction as a selectable output policy, not a fixed template branch.")
    if "debug_preview" in outputs:
        recommendations.append("Keep preview output configurable so monitoring does not define the core graph.")
    if not recommendations:
        recommendations.append("No obvious postprocess/output split was inferred for this representative workflow.")
    return recommendations


def template_candidate(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    con.row_factory = sqlite3.Row

    graph_hash = args.graph_hash or top_graph_hash(con)
    if not graph_hash:
        print("No graph hash found in inventory.")
        return 1

    representative = representative_snapshot_row(con, graph_hash)
    if representative is None:
        print(f"No representative snapshot found for graph `{graph_hash}`.")
        return 1

    artifact_path = Path(str(representative["artifact_path"]))
    source_kind, workflow, prompt = extract_snapshot_payload_from_artifact(artifact_path)
    if workflow is None:
        print(f"Representative artifact does not contain a LiteGraph workflow: `{artifact_path}`")
        return 1

    workflow_summary = summarize_workflow(workflow)
    preset_payload = preset_payloads(workflow_summary)
    fields = _json_loads_maybe(representative["key_fields_json"], {})
    if not fields:
        fields = {
            "models": workflow_summary.get("models") or [],
            "loras": workflow_summary.get("loras") or [],
            "generation": workflow_summary.get("generation") or {},
            "optimization": workflow_summary.get("optimization") or {},
            "io_profile": workflow_summary.get("io_profile") or {},
            "postprocess": workflow_summary.get("postprocess") or {},
            "flags": workflow_summary.get("flags") or [],
        }

    bundle: dict[str, Any] = {
        "schema_version": "comfyui-runpod.template-candidate.v0",
        "created_at": utc_now(),
        "template": {
            "name": args.name or f"graph_{graph_hash[:12]}",
            "status": "candidate",
            "graph_hash": graph_hash,
            "node_count": workflow_summary.get("node_count"),
            "link_count": workflow_summary.get("link_count"),
            "io_profile": workflow_summary.get("io_profile") or {},
        },
        "family": graph_family_stats(con, graph_hash),
        "representative": {
            "artifact_path": str(artifact_path),
            "artifact_ext": representative["artifact_ext"],
            "artifact_kind": representative["artifact_kind"],
            "indexed_source_kind": representative["source_kind"],
            "extracted_source_kind": source_kind,
            "workflow_hash": stable_json_sha256(workflow),
            "prompt_hash": stable_json_sha256(prompt) if prompt is not None else representative["prompt_hash"],
            "recipe_hash": recipe_fingerprint(workflow_summary),
        },
        "preset_hashes": {
            "model": stable_json_sha256(preset_payload["model"]),
            "lora": stable_json_sha256(preset_payload["lora"]),
            "generation": stable_json_sha256(preset_payload["generation"]),
            "optimization": stable_json_sha256(preset_payload["optimization"]),
            "output": stable_json_sha256(preset_payload["output"]),
            "postprocess": stable_json_sha256(preset_payload["postprocess"]),
        },
        "representative_presets": preset_payload,
        "top_preset_variants": preset_variants_for_graph(con, graph_hash, args.variant_limit),
        "node_type_counts": workflow_summary.get("node_type_counts") or {},
        "node_manifest": workflow_node_manifest(workflow),
        "prompt": {
            "included": bool(args.include_prompt and prompt is not None),
            "hash": stable_json_sha256(prompt) if prompt is not None else None,
            "node_type_counts": prompt_node_type_counts(prompt),
        },
        "recommendations": separation_recommendations(fields),
    }
    if args.include_workflow:
        bundle["source_workflow"] = workflow
    if args.include_prompt and prompt is not None:
        bundle["source_prompt"] = prompt

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else Path(args.db).expanduser().resolve().parent / "template_candidates" / f"{graph_hash[:12]}.candidate.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote_template_candidate={output_path}")
    print(f"graph_hash={graph_hash}")
    print(f"representative={artifact_path}")
    return 0


def workflow_from_candidate(candidate: dict[str, Any]) -> Any:
    representative = candidate.get("representative") if isinstance(candidate.get("representative"), dict) else {}
    artifact_path = representative.get("artifact_path")
    if artifact_path:
        _source_kind, workflow, _prompt = extract_snapshot_payload_from_artifact(Path(str(artifact_path)))
        if is_litegraph_workflow(workflow):
            return workflow

    workflow = candidate.get("source_workflow")
    if is_litegraph_workflow(workflow):
        return workflow

    raise RuntimeError("candidate has no usable source_workflow or representative.artifact_path")


def workflow_review_category(node: dict[str, Any]) -> str:
    node_type = str(node.get("type") or node.get("class_type") or "")
    title = str(node.get("title") or "")
    if node_type in {"LoadImage", "VHS_LoadVideo", "VHS_LoadVideoPath", "VHS_LoadVideoFFmpeg", "VHS_LoadVideoFFmpegPath"}:
        return "input"
    if node_type in {"PrimitiveStringMultiline", "CLIPTextEncode", "Florence2Run", "DownloadAndLoadFlorence2Model"}:
        return "prompt"
    if node_type in {
        "UnetLoaderGGUFDisTorchMultiGPU",
        "UnetLoaderGGUF",
        "CLIPLoaderGGUFMultiGPU",
        "CLIPLoaderGGUF",
        "DualCLIPLoaderGGUF",
        "VAELoader",
        "CLIPVisionLoader",
        "UpscaleModelLoader",
    }:
        return "model"
    if node_type == "Power Lora Loader (rgthree)":
        return "lora"
    if node_type in {
        "WanVideoTeaCacheKJ",
        "TorchCompileModelWanVideo",
        "CFGZeroStar",
        "CFGZeroStarAndInit",
        "ApplyRifleXRoPE_WanVideo",
        "SkipLayerGuidanceWanVideo",
        "ModelSamplingSD3",
        "PatchModelPatcherOrder",
        "PathchSageAttentionKJ",
    }:
        return "optimization"
    if node_type in {"WanImageToVideo", "CLIPVisionEncode", "RandomNoise", "BasicScheduler", "KSamplerSelect", "CFGGuider"}:
        return "conditioning"
    if node_type in {"SamplerCustomAdvanced", "SamplerCustomAdvancedLivePreview", "SplitSigmas"}:
        return "sampling"
    if node_type in {"VAEDecode", "VHS_VAEDecodeBatched", "VAEDecodeTiled"}:
        return "decode"
    if node_type in {"VHS_VideoCombine", "SaveImage"}:
        if node.get("mode", 0) in (2, 4) or "up" in title.lower() or "interpol" in title.lower():
            return "postprocess"
        return "output"
    if node_type in {"ImageUpscaleWithModel", "RIFE VFI", "ImageScaleBy", "VHS_MergeImages", "ColorMatch", "ImageFromBatch"}:
        return "postprocess"
    if node_type.startswith("Fast Groups"):
        return "controls"
    return "utility"


def review_title(node: dict[str, Any]) -> Optional[str]:
    node_type = str(node.get("type") or node.get("class_type") or "")
    current_title = str(node.get("title") or "")
    widgets = node.get("widgets_values")

    if node_type == "LoadImage":
        return "INPUT: source image"
    if node_type in {"VHS_LoadVideo", "VHS_LoadVideoPath", "VHS_LoadVideoFFmpeg", "VHS_LoadVideoFFmpegPath"}:
        return "INPUT: source video"
    if node_type == "UnetLoaderGGUFDisTorchMultiGPU":
        return "MODEL: Wan UNet GGUF"
    if node_type in {"CLIPLoaderGGUFMultiGPU", "CLIPLoaderGGUF", "DualCLIPLoaderGGUF"}:
        return "MODEL: text encoder"
    if node_type == "VAELoader":
        return "MODEL: VAE"
    if node_type == "CLIPVisionLoader":
        return "MODEL: CLIP vision"
    if node_type == "Power Lora Loader (rgthree)":
        return "PRESET: LoRA stack"
    if node_type == "PrimitiveStringMultiline":
        lowered = current_title.lower()
        if "negative" in lowered:
            return "PROMPT: negative"
        if "positive" in lowered:
            return "PROMPT: positive"
    if node_type == "CLIPTextEncode":
        lowered = current_title.lower()
        if "negative" in lowered:
            return "ENCODE: negative prompt"
        if "positive" in lowered:
            return "ENCODE: positive prompt"
    if node_type == "WanImageToVideo":
        if isinstance(widgets, list) and len(widgets) >= 3:
            return f"CORE: Wan image-to-video latent ({widgets[0]}x{widgets[1]}, {widgets[2]} frames)"
        return "CORE: Wan image-to-video latent"
    if node_type == "WanVideoTeaCacheKJ":
        return "OPT: TeaCache (enabled default)"
    if node_type == "TorchCompileModelWanVideo":
        return "OPT: TorchCompile (disabled)"
    if node_type in {"CFGZeroStar", "CFGZeroStarAndInit"}:
        return "OPT: CFGZeroStar (disabled)"
    if node_type == "ApplyRifleXRoPE_WanVideo":
        return "OPT: RifleXRoPE (disabled)"
    if node_type == "SkipLayerGuidanceWanVideo":
        return "OPT: Skip Layer Guidance (disabled)"
    if node_type == "BasicScheduler":
        return "SAMPLE: scheduler / steps / denoise"
    if node_type == "KSamplerSelect":
        return "SAMPLE: sampler"
    if node_type == "CFGGuider":
        return "SAMPLE: CFG guider"
    if node_type == "SamplerCustomAdvanced":
        return "SAMPLE: sampler stage"
    if node_type == "VAEDecode":
        return "DECODE: VAE decode"
    if node_type == "VHS_VideoCombine":
        if node.get("mode", 0) in (2, 4):
            return f"DISABLED OUTPUT: {current_title or 'video branch'}"
        lowered = current_title.lower()
        if "preview" in lowered or "raw" in lowered:
            return "OUTPUT: preview/debug MP4"
        return "OUTPUT: final MP4"
    if node_type == "SaveImage":
        return "DISABLED OUTPUT: sample frame"
    if node_type in {"ImageUpscaleWithModel", "ImageScaleBy", "RIFE VFI", "VHS_MergeImages", "ColorMatch", "ImageFromBatch"}:
        return f"POSTPROCESS: {node_type} (kept for review)"
    return None


def apply_review_workflow_edits(workflow: Any, name: str, output_prefix: str, relayout: bool = False) -> dict[str, Any]:
    draft = json.loads(json.dumps(workflow))
    if not is_litegraph_workflow(draft):
        raise RuntimeError("expected LiteGraph workflow")

    category_columns = {
        "input": 0,
        "prompt": 380,
        "model": 760,
        "lora": 1120,
        "optimization": 1480,
        "conditioning": 1840,
        "sampling": 2200,
        "decode": 2560,
        "output": 2920,
        "postprocess": 3280,
        "controls": 3640,
        "utility": 4000,
    }
    category_counts: collections.Counter[str] = collections.Counter()
    changes: collections.Counter[str] = collections.Counter()

    for node in draft.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "")
        category = workflow_review_category(node)
        index = category_counts[category]
        category_counts[category] += 1
        if relayout:
            node["pos"] = [category_columns[category], 80 + index * 150]

        title = review_title(node)
        if title:
            node["title"] = title
            changes["retitled_nodes"] += 1

        if node_type == "LoadImageWithFilename|pysssss":
            node["type"] = "LoadImage"
            props = node.get("properties")
            if isinstance(props, dict):
                props["Node name for S&R"] = "LoadImage"
            outputs = node.get("outputs")
            if isinstance(outputs, list) and len(outputs) > 2:
                node["outputs"] = outputs[:2]
            changes["normalized_load_image"] += 1

        if node_type == "WanVideoTeaCacheKJ":
            node["mode"] = 0
            changes["enabled_teacache"] += 1
        if node_type in {
            "TorchCompileModelWanVideo",
            "CFGZeroStar",
            "CFGZeroStarAndInit",
            "ApplyRifleXRoPE_WanVideo",
            "SkipLayerGuidanceWanVideo",
        }:
            node["mode"] = 4
            changes["disabled_optional_optimizations"] += 1

        if node_type == "VHS_VideoCombine" and isinstance(node.get("widgets_values"), dict):
            widgets = node["widgets_values"]
            if "videopreview" in widgets:
                widgets.pop("videopreview", None)
                changes["stripped_video_previews"] += 1
            if node.get("mode", 0) not in (2, 4) and widgets.get("save_output") is not False:
                suffix = "_PREVIEW" if "preview" in str(node.get("title") or "").lower() else "_FINAL"
                widgets["filename_prefix"] = f"{output_prefix}/{name}{suffix}"
                widgets["save_metadata"] = True
                changes["redirected_active_outputs"] += 1

    if relayout:
        groups = []
        for category, x in category_columns.items():
            if category_counts[category] == 0:
                continue
            groups.append(
                {
                    "id": len(groups) + 1,
                    "title": category.upper(),
                    "bounding": [x - 30, 20, 320, max(180, category_counts[category] * 150 + 80)],
                    "color": "#3f789e" if category not in {"postprocess", "controls", "utility"} else "#8a6f3d",
                    "font_size": 24,
                    "flags": {},
                }
            )
        draft["groups"] = groups
        changes["relayout_groups"] = len(groups)

    return {
        "workflow": draft,
        "changes": dict(changes),
        "category_counts": dict(sorted(category_counts.items())),
    }


def workflow_draft(args: argparse.Namespace) -> int:
    candidate_path = Path(args.candidate).expanduser().resolve()
    candidate = read_json(candidate_path)
    if not isinstance(candidate, dict):
        print(f"Candidate is not a JSON object: `{candidate_path}`")
        return 1

    name = args.name or str((candidate.get("template") or {}).get("name") or candidate_path.stem).replace(".candidate", "")
    if args.source_workflow:
        workflow = read_json(Path(args.source_workflow).expanduser().resolve())
        if not is_litegraph_workflow(workflow):
            print(f"Source workflow is not a LiteGraph workflow: `{args.source_workflow}`")
            return 1
    else:
        workflow = workflow_from_candidate(candidate)
    result = apply_review_workflow_edits(workflow, name=name, output_prefix=args.output_prefix, relayout=args.relayout)

    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else Path(args.db).expanduser().resolve().parent / "workflow_drafts" / f"{name}.review.workflow.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result["workflow"], ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    summary_path = Path(args.db).expanduser().resolve().parent / "workflow_drafts" / f"{name}.summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "created_at": utc_now(),
        "candidate_path": str(candidate_path),
        "workflow_path": str(output_path),
        "name": name,
        "changes": result["changes"],
        "category_counts": result["category_counts"],
        "source_graph_hash": (candidate.get("template") or {}).get("graph_hash"),
        "source_representative": (candidate.get("representative") or {}).get("artifact_path"),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote_workflow_draft={output_path}")
    print(f"wrote_summary={summary_path}")
    print(f"name={name}")
    print(f"changes={json.dumps(result['changes'], sort_keys=True)}")
    return 0


def outliers(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    con.row_factory = sqlite3.Row

    print("# Snowflake Outliers\n")
    print(f"Database: `{args.db}`\n")

    print("## Rare Graph Families")
    for row in _rows(
        con,
        """
        SELECT graph_hash, COUNT(*) AS count, MAX(node_count) AS nodes, MIN(a.path) AS example_path
        FROM workflow_snapshots s
        JOIN artifacts a ON a.id = s.artifact_id
        WHERE graph_hash IS NOT NULL
        GROUP BY graph_hash
        HAVING count <= ?
        ORDER BY count ASC, nodes DESC
        LIMIT ?
        """,
        (args.rare_threshold, args.limit),
    ):
        print(f"- count={row['count']} nodes={row['nodes']} graph=`{row['graph_hash']}` example=`{row['example_path']}`")

    print("\n## High Recipe Drift Graphs")
    for row in _rows(
        con,
        """
        SELECT
            graph_hash,
            COUNT(*) AS artifacts,
            COUNT(DISTINCT recipe_hash) AS recipes,
            COUNT(DISTINCT prompt_hash) AS prompts,
            MIN(a.path) AS example_path
        FROM workflow_snapshots s
        JOIN artifacts a ON a.id = s.artifact_id
        WHERE graph_hash IS NOT NULL
        GROUP BY graph_hash
        HAVING artifacts >= ?
        ORDER BY recipes DESC, artifacts DESC
        LIMIT ?
        """,
        (args.min_family_size, args.limit),
    ):
        print(
            f"- artifacts={row['artifacts']} recipes={row['recipes']} prompts={row['prompts']} "
            f"graph=`{row['graph_hash']}` example=`{row['example_path']}`"
        )

    print("\n## Rare Presets")
    for kind, column in [
        ("model", "model_preset_hash"),
        ("lora", "lora_preset_hash"),
        ("generation", "generation_config_hash"),
        ("optimization", "optimization_preset_hash"),
        ("output", "output_preset_hash"),
        ("postprocess", "postprocess_preset_hash"),
    ]:
        print(f"\n### {kind}")
        for row in _rows(
            con,
            f"""
            SELECT {column} AS hash, COUNT(*) AS count, MIN(a.path) AS example_path
            FROM workflow_snapshots s
            JOIN artifacts a ON a.id = s.artifact_id
            WHERE {column} IS NOT NULL
            GROUP BY {column}
            HAVING count <= ?
            ORDER BY count ASC
            LIMIT ?
            """,
            (args.rare_threshold, args.limit),
        ):
            print(f"- count={row['count']} hash=`{row['hash']}` example=`{row['example_path']}`")

    print("\n## Artifacts Without Workflow Metadata")
    for row in _rows(
        con,
        """
        SELECT ext, status, path, error
        FROM artifacts
        WHERE status != 'ok'
        ORDER BY ext, path
        LIMIT ?
        """,
        (args.limit,),
    ):
        err = f" error={row['error']}" if row["error"] else ""
        print(f"- `{row['ext']}` `{row['status']}` `{row['path']}`{err}")

    print("\n## Optimization Flags")
    flag_counts: collections.Counter[str] = collections.Counter()
    examples: dict[str, str] = {}
    for row in _rows(
        con,
        """
        SELECT a.path, s.key_fields_json
        FROM workflow_snapshots s
        JOIN artifacts a ON a.id = s.artifact_id
        """,
    ):
        fields = _json_loads_maybe(row["key_fields_json"], {})
        for flag in fields.get("flags") or []:
            flag = str(flag)
            flag_counts[flag] += 1
            examples.setdefault(flag, row["path"])
    for flag, count in flag_counts.most_common(args.limit):
        print(f"- `{flag}`: {count}, example=`{examples.get(flag)}`")

    return 0


def inspect(args: argparse.Namespace) -> int:
    con = open_db(Path(args.db))
    con.row_factory = sqlite3.Row

    if args.path:
        rows = _rows(
            con,
            """
            SELECT a.*, s.*
            FROM artifacts a
            LEFT JOIN workflow_snapshots s ON s.artifact_id = a.id
            WHERE a.path = ?
            """,
            (str(Path(args.path).expanduser().resolve()),),
        )
    else:
        column = HASH_COLUMNS[args.kind]
        rows = _rows(
            con,
            f"""
            SELECT a.*, s.*
            FROM workflow_snapshots s
            JOIN artifacts a ON a.id = s.artifact_id
            WHERE s.{column} = ?
            ORDER BY a.path
            LIMIT ?
            """,
            (args.hash, args.examples),
        )

    if not rows:
        print("No matching artifact or snapshot found.")
        return 1

    first = rows[0]
    if args.path:
        print("# Artifact Inspect\n")
        print(f"- path: `{first['path']}`")
        print(f"- status: `{first['status']}`")
        print(f"- ext: `{first['ext']}`")
        print(f"- size_bytes: {first['size_bytes']}")
        print(f"- media: {first['media_width']}x{first['media_height']} duration={first['media_duration']}")
    else:
        print("# Hash Inspect\n")
        print(f"- kind: `{args.kind}`")
        print(f"- hash: `{args.hash}`")
        total = con.execute(
            f"SELECT COUNT(*) FROM workflow_snapshots WHERE {HASH_COLUMNS[args.kind]} = ?",
            (args.hash,),
        ).fetchone()[0]
        print(f"- matching snapshots: {total}")

    if first["id"] is not None and "workflow_hash" in first.keys():
        print("\n## Identities")
        for kind, column in HASH_COLUMNS.items():
            value = first[column] if column in first.keys() else None
            if value:
                print(f"- {kind}: `{value}`")

        print("\n## Representative Fields")
        fields = _json_loads_maybe(first["key_fields_json"], {})
        _print_json_block("fields", fields)
        prompt_counts = _json_loads_maybe(first["prompt_node_type_counts_json"], {})
        if prompt_counts:
            _print_json_block("prompt_node_type_counts", prompt_counts)

    print("\n## Examples")
    for row in rows[: args.examples]:
        print(f"- `{row['path']}` status=`{row['status']}` source=`{row['source_kind']}`")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inventory embedded ComfyUI workflow snowflakes")
    parser.add_argument(
        "--db",
        default="/home/yuji/src/comfyui-runpod/.data/snowflake_inventory.sqlite",
        help="SQLite database path",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan_p = sub.add_parser("scan", help="Scan artifacts into the SQLite index")
    scan_p.add_argument("paths", nargs="+", help="Files or directories to scan")
    scan_p.add_argument("--max-files", type=int, default=None, help="Stop after scanning N matching artifacts")
    scan_p.add_argument("--reset", action="store_true", help="Clear existing index before scanning")
    scan_p.add_argument("--no-store-payloads", action="store_true", help="Store hashes/summaries only, not raw JSON payloads")
    scan_p.add_argument("--progress", type=int, default=500, help="Print progress every N artifacts; 0 disables")
    scan_p.set_defaults(func=scan)

    report_p = sub.add_parser("report", help="Print an inventory report from the SQLite index")
    report_p.add_argument("--limit", type=int, default=20, help="Limit rows in top-N sections")
    report_p.set_defaults(func=report)

    outliers_p = sub.add_parser("outliers", help="Print rare families, rare presets, and metadata gaps")
    outliers_p.add_argument("--limit", type=int, default=20, help="Limit rows in each section")
    outliers_p.add_argument("--rare-threshold", type=int, default=20, help="Maximum count considered rare")
    outliers_p.add_argument("--min-family-size", type=int, default=25, help="Minimum graph family size for drift ranking")
    outliers_p.set_defaults(func=outliers)

    mainline_p = sub.add_parser("mainline", help="Summarize high-coverage graph families and preset layers")
    mainline_p.add_argument("--limit", type=int, default=20, help="Limit rows in each section")
    mainline_p.add_argument(
        "--min-artifacts",
        type=int,
        default=100,
        help="Minimum artifacts for a graph family to be considered mainline",
    )
    mainline_p.set_defaults(func=mainline)

    template_p = sub.add_parser("template-candidate", help="Export a candidate template bundle for a graph family")
    template_p.add_argument("--graph-hash", help="Graph hash to export; defaults to the largest graph family")
    template_p.add_argument("--name", help="Candidate template name")
    template_p.add_argument("--output", help="Output JSON path")
    template_p.add_argument("--variant-limit", type=int, default=10, help="Preset variants to include per layer")
    template_p.add_argument("--include-workflow", action="store_true", help="Include the full representative LiteGraph workflow")
    template_p.add_argument("--include-prompt", action="store_true", help="Include the full representative API prompt when present")
    template_p.set_defaults(func=template_candidate)

    workflow_draft_p = sub.add_parser("workflow-draft", help="Generate a ComfyUI-loadable readable workflow from a candidate")
    workflow_draft_p.add_argument("--candidate", required=True, help="Template candidate JSON path")
    workflow_draft_p.add_argument("--source-workflow", help="Override candidate representative with this LiteGraph workflow JSON")
    workflow_draft_p.add_argument("--name", help="Readable workflow/template name")
    workflow_draft_p.add_argument("--output", help="Output ComfyUI workflow JSON path")
    workflow_draft_p.add_argument(
        "--output-prefix",
        default="workflow-review/%date:yyyy-MM-dd%",
        help="Filename prefix root for active video outputs if the draft is run",
    )
    workflow_draft_p.add_argument("--relayout", action="store_true", help="Re-layout nodes into generated review columns")
    workflow_draft_p.set_defaults(func=workflow_draft)

    inspect_p = sub.add_parser("inspect", help="Inspect one artifact path or hash family")
    inspect_group = inspect_p.add_mutually_exclusive_group(required=True)
    inspect_group.add_argument("--path", help="Artifact path to inspect")
    inspect_group.add_argument("--hash", help="Hash value to inspect")
    inspect_p.add_argument(
        "--kind",
        choices=sorted(HASH_COLUMNS.keys()),
        default="graph",
        help="Hash kind when using --hash",
    )
    inspect_p.add_argument("--examples", type=int, default=5, help="Number of example artifacts to print")
    inspect_p.set_defaults(func=inspect)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
