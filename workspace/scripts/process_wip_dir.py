#!/usr/bin/env python3
"""
Batch-process a wip day folder and write sidecars next to each MP4:

For each *.mp4 in the directory:
- <stem>.preset.json      (compact run parameters extracted from prompt metadata)
- <stem>.metadata.json    (small summary: used_seed + hashes + where extracted from)
- <stem>.workflow.json    (embedded workflow JSON, if available)
- <stem>.XMP              (merge comfy:* seed/hash fields into XMP sidecar)

Notes:
- Extraction prefers MP4 container tags, but falls back to the companion PNG if MP4 tags
  don't contain prompt/workflow JSON.
- By default we don't overwrite existing sidecars (use --overwrite).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import comfy_meta_lib as cml
import clean_comfy_workflow as ccw
import update_comfy_seed_xmp as uxmp


_VARIANT_RE = re.compile(r"_(OG|UPIN)_(\d+)$", re.IGNORECASE)


def _group_key(stem: str) -> str:
    return _VARIANT_RE.sub("", stem)


def _variant_from_stem(stem: str) -> str:
    m = _VARIANT_RE.search(stem)
    return m.group(1).upper() if m else "MP4"


def _extract_from_media_with_png_fallback(mp4_path: Path) -> Tuple[Optional[Any], Optional[Any], str]:
    """
    Returns (prompt_obj, workflow_obj, source) where source is "mp4" or "png".
    """
    tags = cml.ffprobe_format_tags(mp4_path)
    prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_tags(tags)
    if prompt_obj is not None or workflow_obj is not None:
        return prompt_obj, workflow_obj, "mp4"

    png = mp4_path.with_suffix(".png")
    if png.exists():
        chunks = cml.read_png_text_chunks(png)
        prompt_obj, workflow_obj = cml.extract_prompt_workflow_from_png_chunks(chunks)
        return prompt_obj, workflow_obj, "png"

    return None, None, "none"


def _resolve_xmp_path(media_path: Path) -> Path:
    # Prefer existing sidecar if present, preserving case.
    cand1 = media_path.with_suffix(".XMP")
    cand2 = media_path.with_suffix(".xmp")
    if cand1.exists():
        return cand1
    if cand2.exists():
        return cand2
    return cand1


def _write_json(path: Path, obj: Any, *, indent: int = 2, overwrite: bool = False) -> bool:
    if path.exists() and not overwrite:
        return False
    path.write_text(json.dumps(obj, indent=indent, ensure_ascii=False), encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch-generate preset/metadata/workflow/XMP sidecars for a wip folder")
    ap.add_argument("dir", help="Directory containing *.mp4/*.png (e.g. output/output/wip/2026-02-01)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing sidecars")
    ap.add_argument("--indent", type=int, default=2, help="JSON indent level (default: 2)")
    ap.add_argument("--namespace", default=uxmp.DEFAULT_COMFY_NS, help="Namespace URI for comfy:* fields")
    ap.add_argument("--preset-path", default="", help="If set, store comfy:presetPath in XMP")
    ap.add_argument(
        "--template-canonicalize-titles",
        action="store_true",
        default=True,
        help="Canonicalize titles (collapse whitespace) in the generated template JSON (default: true).",
    )
    ap.add_argument(
        "--no-template-canonicalize-titles",
        action="store_false",
        dest="template_canonicalize_titles",
        help="Disable title canonicalization in the generated template JSON.",
    )
    ap.add_argument(
        "--embed-preset-json",
        action="store_true",
        help="Embed comfy:presetJson into XMP (not recommended; can be large).",
    )
    ap.add_argument("--dry-run", action="store_true", help="Do not write, just report what would happen")
    args = ap.parse_args()

    d = Path(args.dir)
    if not d.exists() or not d.is_dir():
        raise SystemExit(f"Not a directory: {d}")

    # Ensure update_comfy_seed_xmp writes the right comfy:* namespace.
    uxmp.COMFY_NS = args.namespace

    mp4s = sorted([p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"])
    if not mp4s:
        raise SystemExit(f"No .mp4 files found in {d}")

    stats = {
        "mp4_files": len(mp4s),
        "ok": 0,
        "failed": 0,
        "used_mp4_metadata": 0,
        "used_png_fallback": 0,
        "wrote_preset_json": 0,
        "wrote_metadata_json": 0,
        "wrote_workflow_json": 0,
        "wrote_template_json": 0,
        "updated_xmp": 0,
        "skipped_existing": 0,
    }
    failures: Dict[str, str] = {}
    compare = {
        "unique_prompt_sha256": set(),
        "unique_workflow_sha256": set(),
        "unique_template_sha256": set(),
        "unique_preset_sha256": set(),
    }
    compare_pairs = {"og_upin_preset_mismatch": 0, "og_upin_template_mismatch": 0, "og_upin_workflow_mismatch": 0}
    by_group: Dict[str, Dict[str, Dict[str, Optional[str]]]] = {}

    for mp4 in mp4s:
        try:
            prompt_obj, workflow_obj, source = _extract_from_media_with_png_fallback(mp4)
            if source == "mp4":
                stats["used_mp4_metadata"] += 1
            elif source == "png":
                stats["used_png_fallback"] += 1

            if prompt_obj is None and workflow_obj is None:
                raise RuntimeError("No embedded prompt/workflow metadata found in MP4 tags or companion PNG")

            # preset.json
            preset_obj = cml.extract_preset(prompt_obj)
            if preset_obj is None:
                raise RuntimeError("Prompt metadata present but could not extract preset (unexpected prompt format)")

            preset_path = mp4.with_suffix(".preset.json")
            meta_path = mp4.with_suffix(".metadata.json")
            workflow_path = mp4.with_suffix(".workflow.json")
            template_path = mp4.with_suffix(".template.cleaned.json")
            xmp_path = _resolve_xmp_path(mp4)

            # metadata.json (compact + stable)
            seeds = cml.collect_seeds_from_prompt(prompt_obj)
            template_obj = (
                ccw.clean_workflow(workflow_obj, canonicalize_titles=args.template_canonicalize_titles)
                if isinstance(workflow_obj, dict)
                else None
            )
            metadata_obj = {
                "source_media": mp4.name,
                "extracted_from": source,
                "used_seed": seeds.get("used_seed"),
                "seed_source": seeds.get("seed_source"),
                "noise_seeds": seeds.get("noise_seeds"),
                "ksampler_seeds": seeds.get("ksampler_seeds"),
                "prompt_sha256": cml.stable_json_sha256(prompt_obj) if prompt_obj is not None else None,
                "workflow_sha256": cml.stable_json_sha256(workflow_obj) if workflow_obj is not None else None,
                "preset_sha256": cml.stable_json_sha256(preset_obj),
                "template_sha256": cml.stable_json_sha256(template_obj) if template_obj is not None else None,
            }

            # Comparison bookkeeping (for "quick comparison" summary)
            if metadata_obj["prompt_sha256"]:
                compare["unique_prompt_sha256"].add(metadata_obj["prompt_sha256"])
            if metadata_obj["workflow_sha256"]:
                compare["unique_workflow_sha256"].add(metadata_obj["workflow_sha256"])
            if metadata_obj["preset_sha256"]:
                compare["unique_preset_sha256"].add(metadata_obj["preset_sha256"])
            if metadata_obj["template_sha256"]:
                compare["unique_template_sha256"].add(metadata_obj["template_sha256"])

            gkey = _group_key(mp4.stem)
            variant = _variant_from_stem(mp4.stem)
            by_group.setdefault(gkey, {})[variant] = {
                "preset_sha256": metadata_obj["preset_sha256"],
                "template_sha256": metadata_obj["template_sha256"],
                "workflow_sha256": metadata_obj["workflow_sha256"],
            }

            if args.dry_run:
                stats["ok"] += 1
                continue

            wrote_any = False

            if _write_json(preset_path, preset_obj, indent=args.indent, overwrite=args.overwrite):
                stats["wrote_preset_json"] += 1
                wrote_any = True
            else:
                stats["skipped_existing"] += 1

            if _write_json(meta_path, metadata_obj, indent=args.indent, overwrite=args.overwrite):
                stats["wrote_metadata_json"] += 1
                wrote_any = True
            else:
                stats["skipped_existing"] += 1

            if workflow_obj is not None:
                if _write_json(workflow_path, workflow_obj, indent=args.indent, overwrite=args.overwrite):
                    stats["wrote_workflow_json"] += 1
                    wrote_any = True
                else:
                    stats["skipped_existing"] += 1

            if template_obj is not None:
                if _write_json(template_path, template_obj, indent=args.indent, overwrite=args.overwrite):
                    stats["wrote_template_json"] += 1
                    wrote_any = True
                else:
                    stats["skipped_existing"] += 1

            # Update XMP (always safe-merge; overwrite doesn't really apply the same way)
            comfy_fields: Dict[str, str] = {}
            if metadata_obj["used_seed"] is not None:
                comfy_fields["usedSeed"] = str(metadata_obj["used_seed"])
            if metadata_obj["seed_source"]:
                comfy_fields["seedSource"] = str(metadata_obj["seed_source"])
            if seeds.get("noise_seeds"):
                comfy_fields["noiseSeed"] = str(seeds["noise_seeds"][0])
            if seeds.get("ksampler_seeds"):
                comfy_fields["ksamplerSeed"] = str(seeds["ksampler_seeds"][0])
            if metadata_obj["prompt_sha256"]:
                comfy_fields["promptSha256"] = str(metadata_obj["prompt_sha256"])
            if metadata_obj["workflow_sha256"]:
                comfy_fields["workflowSha256"] = str(metadata_obj["workflow_sha256"])
            if metadata_obj["preset_sha256"]:
                comfy_fields["presetSha256"] = str(metadata_obj["preset_sha256"])
            if metadata_obj["template_sha256"]:
                comfy_fields["templateSha256"] = str(metadata_obj["template_sha256"])
            if args.preset_path:
                comfy_fields["presetPath"] = args.preset_path
            if args.embed_preset_json:
                comfy_fields["presetJson"] = cml.json_min(preset_obj) or ""

            if comfy_fields:
                uxmp.update_xmp_in_place(xmp_path, comfy_fields)
                stats["updated_xmp"] += 1
                wrote_any = True

            stats["ok"] += 1
            _ = wrote_any
        except Exception as e:
            stats["failed"] += 1
            failures[mp4.name] = str(e)

    print(json.dumps({"dir": str(d), "stats": stats, "failures": failures}, indent=2, ensure_ascii=False))
    # Quick comparison summary (how many distinct values we saw across the folder)
    summary = {
        "unique_prompt_sha256": len(compare["unique_prompt_sha256"]),
        "unique_workflow_sha256": len(compare["unique_workflow_sha256"]),
        "unique_preset_sha256": len(compare["unique_preset_sha256"]),
        "unique_template_sha256": len(compare["unique_template_sha256"]),
    }

    # OG vs UPIN per-group comparisons
    for _, m in by_group.items():
        og = m.get("OG")
        up = m.get("UPIN")
        if not og or not up:
            continue
        if og.get("preset_sha256") != up.get("preset_sha256"):
            compare_pairs["og_upin_preset_mismatch"] += 1
        if og.get("template_sha256") != up.get("template_sha256"):
            compare_pairs["og_upin_template_mismatch"] += 1
        if og.get("workflow_sha256") != up.get("workflow_sha256"):
            compare_pairs["og_upin_workflow_mismatch"] += 1

    print(json.dumps({"comparison": summary, "og_vs_upin": compare_pairs}, indent=2, ensure_ascii=False))
    return 0 if stats["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

