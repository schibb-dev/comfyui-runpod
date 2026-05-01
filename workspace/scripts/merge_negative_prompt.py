#!/usr/bin/env python3
"""
Negatives process (intended behavior):

1. The workflow should use: (a) the negatives file(s) and (b) the experimental
   negatives field, and from those populate a read-only "Negative" field that is
   fed to negative conditioning. You never edit that Negative node by hand.

2. You manage the negatives files directly. Nothing in this pipeline writes to
   those files.

3. Role of this script:
   - ComfyUI workflows are static JSON; there is no built-in node that "loads a
     file and merges with a string" at runtime. So the script is the step that
     performs that merge before the workflow runs.
   - What it does: run before queue (or before loading the workflow). It reads
     from the workflow JSON the "Negative base file" (path/profile) and
     "Negative (experimental)" text, loads the base content from the negatives
     file(s) on disk, merges base + experimental into a composite, and writes that
     composite into the "Negative" node in the workflow JSON. When ComfyUI (or
     your runner) then loads the workflow, the Negative node already contains the
     composite — so the read-only Negative field is correctly populated from
     file + experimental.
   - The script only updates the workflow's Negative node. It never modifies the
     negatives file(s).
   - You can instead use the custom node "Negative Prompt Merge (file + experimental)"
     (NegativePromptMerge) in the workflow: it loads the file and experimental at
     runtime and outputs the composite string; connect that to CLIPTextEncode (negative).
     Then no pre-run script is needed.

Options:
- Base file: set in workflow "Negative base file" or override with --base-file / BASE_NEGATIVES_FILE.
- Leading "-" in base file field = disabled (base not loaded).
- If base file does not exist: skip and use only experimental.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

# Node titles the script looks for (exact match after strip)
TITLE_BASE_FILE = "Negative base file"
TITLE_EXPERIMENTAL = "Negative (experimental)"
TITLE_NEGATIVE = "Negative"
TITLE_NEGATIVE_ALT = "PROMPT_Negative"

# Leading minus = disabled (rest of value is preserved)
DISABLED_PREFIX = "-"


def _workflow_dir(workflow_path: Path) -> Path:
    return workflow_path.resolve().parent


def _find_node_by_title(workflow: Dict[str, Any], title: str, node_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return None
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if node_type and n.get("type") != node_type:
            continue
        t = (n.get("title") or "").strip()
        if t == title:
            return n
    return None


def _get_text_from_multiline(node: Dict[str, Any]) -> str:
    wv = node.get("widgets_values")
    if not isinstance(wv, list) or len(wv) < 1:
        return ""
    t = wv[0]
    return t if isinstance(t, str) else ""


def _set_text_on_multiline(node: Dict[str, Any], text: str) -> None:
    wv = list(node.get("widgets_values") or [""])
    if not wv:
        wv.append("")
    wv[0] = text
    node["widgets_values"] = wv


def _parse_base_file_value(value: str) -> Tuple[bool, str]:
    """Returns (disabled, path_or_profile). Leading '-' means disabled; value is preserved."""
    raw = (value or "").strip()
    if raw.startswith(DISABLED_PREFIX):
        rest = raw[len(DISABLED_PREFIX):].strip()
        # optional space after minus
        if rest.startswith(" "):
            rest = rest[1:].strip()
        return True, rest
    return False, raw


def _load_negative_sets(root: Path) -> Dict[str, Any]:
    path = root / "negative_sets.yaml"
    if not path.exists() or not yaml:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _resolve_path_and_blocks(root: Path, path_or_profile: str, sets: Dict[str, Any]) -> Tuple[Optional[Path], Optional[List[str]]]:
    """Resolve to (absolute path to file, optional list of block names for YAML)."""
    if not path_or_profile.strip():
        return None, None
    # Profile lookup
    profile = sets.get(path_or_profile.strip())
    if isinstance(profile, dict):
        f = profile.get("file")
        blocks = profile.get("blocks")
        if isinstance(f, str):
            p = (root / f).resolve()
            return p, blocks if isinstance(blocks, list) else None
        return None, None
    if isinstance(profile, str):
        return (root / profile).resolve(), None
    # Direct path (string contains / or \ or ends with .txt/.yaml)
    raw = path_or_profile.strip()
    p = (root / raw).resolve()
    return p, None


def _load_base_content(path: Path, blocks: Optional[List[str]]) -> str:
    """Load base negative content from file. If blocks given, treat as YAML and concatenate those keys."""
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if blocks and path.suffix.lower() in (".yaml", ".yml") and yaml:
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict):
                combined = " ".join((data.get(b) or "").strip() for b in blocks)
                parts = [s.strip() for s in re.split(r"[\n,]+", combined) if s.strip()]
                return ", ".join(parts)
        except Exception:
            pass
    if not blocks and path.suffix.lower() in (".yaml", ".yml") and yaml:
        try:
            data = yaml.safe_load(text)
            if isinstance(data, dict):
                parts = []
                for v in data.values():
                    if isinstance(v, str):
                        parts.append(v.strip())
                return ", ".join(p for p in parts if p)
        except Exception:
            pass
    return text.strip()


def merge_negatives(
    workflow: Dict[str, Any],
    workflow_path: Path,
    base_file_override: Optional[str] = None,
) -> Tuple[str, str, str]:
    """
    Merge base (from file) + experimental into composite; patch Negative node.
    Returns (base_content, experimental_content, composite).
    """
    root = _workflow_dir(workflow_path)
    sets = _load_negative_sets(root)

    # Resolve base file source (override > workflow)
    base_value = ""
    node_base = _find_node_by_title(workflow, TITLE_BASE_FILE)
    if node_base:
        base_value = _get_text_from_multiline(node_base) if node_base.get("type") == "PrimitiveStringMultiline" else ""
        # PrimitiveStringMultiline has widgets_values[0]; if it's a single-line we still use that
        if not isinstance(base_value, str):
            base_value = str(base_value or "").strip()
    if base_file_override is not None:
        base_value = base_file_override.strip()

    disabled, path_or_profile = _parse_base_file_value(base_value)
    base_content = ""
    if not disabled and path_or_profile:
        path, blocks = _resolve_path_and_blocks(root, path_or_profile, sets)
        if path and path.exists():
            base_content = _load_base_content(path, blocks)
        # else: file missing → skip (base_content stays "")

    # Experimental
    experimental = ""
    node_exp = _find_node_by_title(workflow, TITLE_EXPERIMENTAL)
    if node_exp:
        experimental = _get_text_from_multiline(node_exp).strip()

    # Composite: base then experimental
    parts = [p for p in (base_content.strip(), experimental) if p]
    composite = "\n\n".join(parts) if parts else ""

    # Write to Negative node (the one used for conditioning)
    node_neg = _find_node_by_title(workflow, TITLE_NEGATIVE) or _find_node_by_title(workflow, TITLE_NEGATIVE_ALT)
    if node_neg:
        _set_text_on_multiline(node_neg, composite)

    return base_content, experimental, composite


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Merge base negatives (file) + experimental into workflow Negative node"
    )
    ap.add_argument("workflow", type=Path, help="Workflow JSON path")
    ap.add_argument("--out", type=Path, default=None, help="Output path (default: overwrite workflow)")
    ap.add_argument("--base-file", type=str, default=None, help="Override base negatives file path or profile")
    ap.add_argument("--dry-run", action="store_true", help="Print composite and exit without writing")
    ap.add_argument("--indent", type=int, default=2)
    args = ap.parse_args()

    env_override = os.environ.get("BASE_NEGATIVES_FILE")
    base_override = args.base_file if args.base_file is not None else (env_override if env_override else None)

    path = args.workflow
    if not path.exists():
        raise SystemExit(f"Workflow not found: {path}")

    workflow = json.loads(path.read_text(encoding="utf-8"))
    base, exp, composite = merge_negatives(workflow, path, base_override)

    if args.dry_run:
        def _safe(s: str, n: int) -> str:
            out = s[:n] + ("..." if len(s) > n else "")
            return out.encode("ascii", errors="replace").decode("ascii")
        print("--- base (from file) ---")
        print(_safe(base, 500))
        print("\n--- experimental ---")
        print(_safe(exp, 500))
        print("\n--- composite (first 1500 chars) ---")
        print(_safe(composite, 1500))
        return 0

    out = args.out or path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(workflow, indent=args.indent, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"out": str(out), "base_len": len(base), "experimental_len": len(exp), "composite_len": len(composite)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
