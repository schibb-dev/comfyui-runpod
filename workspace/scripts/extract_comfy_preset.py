#!/usr/bin/env python3
"""
Extract a compact "preset" JSON (run parameters) from ComfyUI-embedded metadata in an MP4 or PNG.

This is meant to be the standalone run-parameters file you can archive (or even commit),
separate from the workflow template JSON and separate from XMP.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from comfy_meta_lib import (
    extract_preset,
    extract_prompt_workflow_from_png_chunks,
    extract_prompt_workflow_from_tags,
    ffprobe_format_tags,
    read_png_text_chunks,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract preset JSON from MP4/PNG ComfyUI metadata")
    ap.add_argument("media", help="Path to MP4 or PNG")
    ap.add_argument("--out", default="", help="Output JSON path (default: print to stdout)")
    ap.add_argument("--indent", type=int, default=2)
    args = ap.parse_args()

    p = Path(args.media)
    if not p.exists():
        ap.error(f"Not found: {p}")

    if p.suffix.lower() == ".png":
        chunks = read_png_text_chunks(p)
        prompt_obj, _ = extract_prompt_workflow_from_png_chunks(chunks)
    else:
        tags = ffprobe_format_tags(p)
        prompt_obj, _ = extract_prompt_workflow_from_tags(tags)

    if not prompt_obj:
        raise SystemExit("No ComfyUI prompt metadata found. Try the companion PNG, or ensure save_metadata is enabled.")

    preset = extract_preset(prompt_obj)
    if preset is None:
        raise SystemExit("No preset could be extracted from prompt metadata (unexpected format).")
    payload = json.dumps(preset, indent=args.indent, ensure_ascii=False)

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

