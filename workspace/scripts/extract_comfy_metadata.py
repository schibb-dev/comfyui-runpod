#!/usr/bin/env python3
"""
Extract ComfyUI metadata embedded in PNGs (prompt/workflow/iTXt/tEXt) and,
optionally, MP4 container tags (via ffprobe).

Why this exists:
- ComfyUI (and VideoHelperSuite) often saves a companion `*_00001.png` next to an
  `*.mp4`. The PNG usually contains the *exact* resolved prompt/workflow used,
  including the actual numeric seed (even if the workflow UI showed "randomize").

Safety:
- By default this script REDACTS large text fields (like prompt/workflow) so you
  can safely print a summary to terminal.
- Use --no-redact if you want the full raw metadata written to disk for local review.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from comfy_meta_lib import (
    ffprobe_show_format,
    read_png_text_chunks,
)

def _find_seed_values_in_comfy_prompt(prompt_obj: Any) -> List[int]:
    seeds: set[int] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for kk, vv in x.items():
                if kk in ("seed", "noise_seed") and isinstance(vv, int):
                    seeds.add(vv)
                walk(vv)
        elif isinstance(x, list):
            for vv in x:
                walk(vv)

    walk(prompt_obj)
    return sorted(seeds)


def _summarize_png_metadata(text_chunks: Dict[str, str]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "keys": sorted(text_chunks.keys()),
        "seeds_from_prompt_json": [],
        "seed_like_regex_hits": [],
    }

    # Parse ComfyUI API prompt JSON (usually stored under key "prompt")
    prompt_raw = text_chunks.get("prompt")
    if prompt_raw:
        try:
            prompt_obj = json.loads(prompt_raw)
            summary["seeds_from_prompt_json"] = _find_seed_values_in_comfy_prompt(prompt_obj)
        except Exception as e:
            summary["prompt_json_parse_error"] = str(e)

    # Quick seed-like regex hits across all chunks
    seed_hits: set[int] = set()
    for v in text_chunks.values():
        for s in re.findall(r'"seed"\s*:\s*(\d+)', v):
            seed_hits.add(int(s))
        for s in re.findall(r"\bSeed\b\s*[:=]\s*(\d+)", v):
            seed_hits.add(int(s))
    summary["seed_like_regex_hits"] = sorted(seed_hits)

    return summary


def _ffprobe_tags(mp4_path: Path) -> Dict[str, Any]:
    obj = ffprobe_show_format(mp4_path)
    tags = (obj.get("format") or {}).get("tags") or {}
    return {"ffprobe_format": obj.get("format"), "tags": tags}


def _extract_comfy_from_container_tags(tags: Dict[str, Any]) -> Dict[str, Any]:
    """
    Heuristics: some pipelines embed ComfyUI prompt/workflow JSON into MP4 tags
    (often under keys like comment/description).
    """
    found: Dict[str, Any] = {
        "found_prompt": None,
        "found_workflow": None,
        "found_prompt_tag": None,
        "found_workflow_tag": None,
        "json_like_tags": [],
    }

    # First pass: direct keys if present.
    for k in ("prompt", "workflow"):
        v = tags.get(k)
        if isinstance(v, str):
            obj = maybe_json(v)
            if obj is not None:
                found[f"found_{k}"] = obj
                found[f"found_{k}_tag"] = k

    # Second pass: search other text tags for JSON that looks like ComfyUI.
    for k, v in tags.items():
        if not isinstance(v, str):
            continue
        obj = maybe_json(v)
        if obj is None:
            continue

        found["json_like_tags"].append(k)

        # ComfyUI API prompt JSON is a dict keyed by node ids (strings of ints)
        # with entries like {"class_type": "...", "inputs": {...}}
        if found["found_prompt"] is None and isinstance(obj, dict) and obj:
            # quick signature check
            any_node = next(iter(obj.values()))
            if (
                isinstance(any_node, dict)
                and ("class_type" in any_node or "inputs" in any_node)
                and all(isinstance(kk, str) for kk in obj.keys())
            ):
                found["found_prompt"] = obj
                found["found_prompt_tag"] = k
                continue

        # Workflow JSON is typically a dict with keys: nodes, links, groups, extra, version
        if found["found_workflow"] is None and isinstance(obj, dict):
            if "nodes" in obj and "links" in obj and ("groups" in obj or "extra" in obj):
                found["found_workflow"] = obj
                found["found_workflow_tag"] = k
                continue

    return found


def _summarize_container_metadata(container: Dict[str, Any]) -> Dict[str, Any]:
    tags = (container.get("tags") or {}) if isinstance(container, dict) else {}
    extracted = _extract_comfy_from_container_tags(tags)
    summary: Dict[str, Any] = {
        "tag_keys": sorted(tags.keys()),
        "prompt_tag": extracted.get("found_prompt_tag"),
        "workflow_tag": extracted.get("found_workflow_tag"),
        "seeds_from_prompt_json": [],
        "seed_like_regex_hits": [],
    }

    prompt_obj = extracted.get("found_prompt")
    if prompt_obj is not None:
        try:
            summary["seeds_from_prompt_json"] = _find_seed_values_in_comfy_prompt(prompt_obj)
        except Exception as e:
            summary["prompt_seed_extract_error"] = str(e)

    # Regex seed-like hits across all tags (cheap + works even without JSON)
    seed_hits: set[int] = set()
    for v in tags.values():
        if not isinstance(v, str):
            continue
        for s in re.findall(r'"seed"\s*:\s*(\d+)', v):
            seed_hits.add(int(s))
        for s in re.findall(r"\bSeed\b\s*[:=]\s*(\d+)", v):
            seed_hits.add(int(s))
    summary["seed_like_regex_hits"] = sorted(seed_hits)

    # Keep some debugging info without dumping huge strings.
    summary["json_like_tags"] = sorted(set(extracted.get("json_like_tags") or []))
    summary["has_embedded_prompt_json"] = extracted.get("found_prompt") is not None
    summary["has_embedded_workflow_json"] = extracted.get("found_workflow") is not None
    return summary


def _redact_value(key: str, value: Any) -> Any:
    # Redact potentially huge or sensitive content by default, but keep structure.
    if not isinstance(value, str):
        return value

    lk = key.lower()
    if lk in {"prompt", "workflow"}:
        return {"redacted": True, "length": len(value)}

    # Generic: if it's massive, redact it.
    if len(value) > 2000:
        return {"redacted": True, "length": len(value)}

    return value


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract ComfyUI metadata from PNG/MP4")
    ap.add_argument("path", help="Path to PNG or MP4 (or a companion PNG next to MP4)")
    ap.add_argument(
        "--out",
        help="Write extracted metadata JSON to this file (default: print to stdout)",
        default="",
    )
    ap.add_argument(
        "--no-redact",
        help="Do not redact large text fields (prompt/workflow). Use for local review.",
        action="store_true",
    )
    ap.add_argument(
        "--include-summary",
        help="Include a small summary (keys + seed candidates).",
        action="store_true",
        default=True,
    )
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        ap.error(f"File not found: {p}")

    ext = p.suffix.lower()
    out: Dict[str, Any] = {"source_path": str(p)}

    if ext == ".png":
        text_chunks = read_png_text_chunks(p)
        out["png_text_chunks"] = text_chunks
        if args.include_summary:
            out["summary"] = _summarize_png_metadata(text_chunks)

    elif ext in {".mp4", ".mov", ".mkv", ".webm"}:
        # Try MP4 tags
        out["container"] = _ffprobe_tags(p)
        if args.include_summary:
            out["summary"] = _summarize_container_metadata(out["container"])
        # If there's a companion PNG, mention it.
        companion = p.with_suffix(".png")
        if companion.exists():
            out["companion_png"] = str(companion)
            out["companion_png_text_chunks_present"] = True
        else:
            out["companion_png_text_chunks_present"] = False
    else:
        ap.error("Unsupported file type. Use a PNG or MP4/MOV/MKV/WEBM.")

    if not args.no_redact:
        # Shallow-redact the big fields (prompt/workflow) for safe console output.
        if "png_text_chunks" in out:
            out["png_text_chunks"] = {
                k: _redact_value(k, v) for k, v in (out["png_text_chunks"] or {}).items()
            }
        if "container" in out and isinstance(out["container"], dict):
            tags = out["container"].get("tags")
            if isinstance(tags, dict):
                out["container"]["tags"] = {k: _redact_value(k, v) for k, v in tags.items()}
            ff_fmt = out["container"].get("ffprobe_format")
            if isinstance(ff_fmt, dict) and isinstance(ff_fmt.get("tags"), dict):
                ff_fmt["tags"] = {k: _redact_value(k, v) for k, v in ff_fmt["tags"].items()}

    payload = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(str(out_path))
    else:
        print(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

