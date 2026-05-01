#!/usr/bin/env python3
"""
Embed ComfyUI prompt/workflow JSON into an MP4's container tags.

Why:
- Some MP4s lose metadata during re-encode.
- Our tuning tools expect both `prompt` and `workflow` JSON to be present in ffprobe format tags.

This script reads prompt/workflow JSON from files (typically `base/base.prompt.json` and
`base/base.workflow.json`) and muxes them into a copy of the MP4 via ffmpeg stream copy.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_min(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Embed prompt/workflow JSON into MP4 tags")
    ap.add_argument("input_mp4", type=Path)
    ap.add_argument("--prompt-json", required=True, type=Path, help="Path to prompt JSON (resolved prompt dict)")
    ap.add_argument("--workflow-json", required=True, type=Path, help="Path to workflow JSON (nodes/links graph)")
    ap.add_argument("--output-mp4", required=True, type=Path, help="Output MP4 path (will be overwritten)")
    args = ap.parse_args()

    inp: Path = args.input_mp4
    prompt_p: Path = args.prompt_json
    workflow_p: Path = args.workflow_json
    out: Path = args.output_mp4

    if not inp.exists():
        raise SystemExit(f"Missing input mp4: {inp}")
    if not prompt_p.exists():
        raise SystemExit(f"Missing prompt json: {prompt_p}")
    if not workflow_p.exists():
        raise SystemExit(f"Missing workflow json: {workflow_p}")

    prompt_obj = _read_json(prompt_p)
    workflow_obj = _read_json(workflow_p)

    prompt_min = _json_min(prompt_obj)
    workflow_min = _json_min(workflow_obj)

    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(inp),
        "-map",
        "0",
        "-c",
        "copy",
        "-metadata",
        f"prompt={prompt_min}",
        "-metadata",
        f"workflow={workflow_min}",
        # Help ffmpeg write tags in a format ffprobe surfaces as container tags.
        "-movflags",
        "use_metadata_tags",
        str(out),
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg failed (rc={proc.returncode}):\n{proc.stderr.strip()}")

    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

