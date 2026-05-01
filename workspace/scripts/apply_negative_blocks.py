#!/usr/bin/env python3
"""
Inject curated negative-prompt blocks (intruders, deformities) into a ComfyUI workflow.

Reduces whack-a-mole: maintain one negative_prompt_blocks.yaml and apply it to any
workflow that has a "Negative" (or "PROMPT_Negative") PrimitiveStringMultiline node.

Usage:
  python scripts/apply_negative_blocks.py workflow.json --out workflow_with_blocks.json
  python scripts/apply_negative_blocks.py workflow.json --blocks intruders --out out.json
  python scripts/apply_negative_blocks.py workflow.json --blocks intruders deformities --dry-run

If the negative already contains an "Applied blocks" section, it is replaced with
the current block content so re-running updates instead of duplicating.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default blocks file next to workflows (or in workspace)
DEFAULT_BLOCKS_PATH = Path(__file__).resolve().parent.parent / "workspace" / "comfyui_user" / "default" / "workflows" / "negative_prompt_blocks.yaml"

APPLIED_MARKER = "# --- Applied blocks:"
MARKER_PATTERN = re.compile(r"\n?\s*# --- Applied blocks:.*", re.DOTALL)


def load_blocks(path: Path) -> Dict[str, str]:
    try:
        import yaml
    except ImportError:
        raise SystemExit("PyYAML required: pip install pyyaml")

    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(v, str):
            out[k] = v.strip()
        elif v is None:
            continue
        else:
            out[k] = str(v).strip()
    return out


def find_negative_node(workflow: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return None
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") != "PrimitiveStringMultiline":
            continue
        title = (n.get("title") or "").strip()
        if title in ("Negative", "PROMPT_Negative"):
            return n
    return None


def get_negative_text(node: Dict[str, Any]) -> str:
    wv = node.get("widgets_values")
    if not isinstance(wv, list) or len(wv) < 1:
        return ""
    t = wv[0]
    return t if isinstance(t, str) else ""


def set_negative_text(node: Dict[str, Any], text: str) -> None:
    wv = list(node.get("widgets_values") or [""])
    if not wv:
        wv.append("")
    wv[0] = text
    node["widgets_values"] = wv


def merge_blocks_into_negative(current: str, block_names: List[str], blocks: Dict[str, str]) -> str:
    # Remove any existing "Applied blocks" section so we replace it with current block set.
    base = MARKER_PATTERN.sub("", current).rstrip()

    combined = " ".join((blocks.get(name) or "").strip() for name in block_names)
    # Normalize to single comma-separated line (blocks may be multiline or comma-separated).
    parts = [s.strip() for s in re.split(r"[\n,]+", combined) if s.strip()]
    block_text = ", ".join(parts) if parts else ""

    if not block_text:
        return base

    applied_section = f"\n\n{APPLIED_MARKER} {', '.join(block_names)} ---\n{block_text}"
    return base + applied_section


def apply_negative_blocks(
    workflow: Dict[str, Any],
    blocks_path: Path,
    block_names: List[str],
) -> bool:
    blocks = load_blocks(blocks_path)
    node = find_negative_node(workflow)
    if not node:
        return False
    current = get_negative_text(node)
    new_text = merge_blocks_into_negative(current, block_names, blocks)
    set_negative_text(node, new_text)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Inject negative-prompt blocks (intruders, deformities) into a ComfyUI workflow"
    )
    ap.add_argument("workflow", type=Path, help="Path to workflow JSON")
    ap.add_argument("--out", type=Path, default=None, help="Output workflow path (default: overwrite)")
    ap.add_argument(
        "--blocks",
        nargs="+",
        default=["intruders", "deformities"],
        metavar="NAME",
        help="Block names to apply (default: intruders deformities)",
    )
    ap.add_argument("--blocks-file", type=Path, default=None, help=f"YAML blocks file (default: {DEFAULT_BLOCKS_PATH})")
    ap.add_argument("--dry-run", action="store_true", help="Print merged negative and exit")
    ap.add_argument("--indent", type=int, default=2)
    args = ap.parse_args()

    blocks_path = args.blocks_file or DEFAULT_BLOCKS_PATH
    if not blocks_path.exists():
        raise SystemExit(f"Blocks file not found: {blocks_path}")

    workflow_path = args.workflow
    if not workflow_path.exists():
        raise SystemExit(f"Workflow not found: {workflow_path}")

    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    blocks = load_blocks(blocks_path)
    node = find_negative_node(workflow)
    if not node:
        raise SystemExit("No Negative / PROMPT_Negative node found in workflow.")

    current = get_negative_text(node)
    new_text = merge_blocks_into_negative(current, args.blocks, blocks)
    set_negative_text(node, new_text)

    if args.dry_run:
        print("--- Merged negative (first 2000 chars) ---")
        print(new_text[:2000])
        if len(new_text) > 2000:
            print("...")
        return 0

    out_path = args.out or workflow_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(workflow, indent=args.indent, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({"out": str(out_path), "blocks_applied": args.blocks}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
