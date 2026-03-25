#!/usr/bin/env python3
"""
Rewrite ComfyUI **UI workflow** JSON node `type` fields using workflow_node_id_map.yaml.

Updates:
  - nodes[].type
  - nodes[].properties["Node name for S&R"] when it equals the old type

Does not rewrite API prompt JSON (class_type dict); use only for exported graph workflows.

Usage:
  python migrate_workflow_node_types.py --workflow /path/to/wf.json --dry-run
  python migrate_workflow_node_types.py --workflow /path/to/wf.json --output /path/to/wf.migrated.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def load_map(path: Path) -> Dict[str, str]:
    if not path.is_file():
        raise SystemExit(f"Map file not found: {path}")
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise SystemExit("PyYAML required for .yaml maps: pip install pyyaml")
        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    m = data.get("mappings") or {}
    return {str(k): str(v) for k, v in m.items()}


def migrate_workflow(obj: Dict[str, Any], mapping: Dict[str, str]) -> Tuple[int, List[str]]:
    """Apply mapping in place. Returns (replacement_count, sorted list of types in workflow not listed in mapping)."""
    replaced = 0
    nodes = obj.get("nodes")
    if not isinstance(nodes, list):
        return 0, []

    seen_types: Set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        old = node.get("type")
        if not isinstance(old, str):
            continue
        seen_types.add(old)
        if old not in mapping:
            continue
        new = mapping[old]
        if old == new:
            continue
        node["type"] = new
        replaced += 1
        props = node.get("properties")
        if not isinstance(props, dict):
            props = {}
            node["properties"] = props
        if props.get("Node name for S&R") == old:
            props["Node name for S&R"] = new

    unmapped_rules = sorted(t for t in seen_types if t not in mapping)
    return replaced, unmapped_rules


def main() -> int:
    here = Path(__file__).resolve().parent
    default_map = here / "workflow_node_id_map.yaml"

    ap = argparse.ArgumentParser(description="Migrate ComfyUI workflow node type strings.")
    ap.add_argument("--workflow", "-w", required=True, help="Path to workflow JSON")
    ap.add_argument(
        "--map",
        "-m",
        default=str(default_map),
        help=f"YAML/JSON mapping file (default: {default_map.name})",
    )
    ap.add_argument("--output", "-o", help="Write migrated JSON here")
    ap.add_argument("--dry-run", action="store_true", help="Do not write; print stats only")
    ap.add_argument(
        "--list-unmapped",
        action="store_true",
        help="List distinct node types in the workflow that have no row in the map (not necessarily errors)",
    )
    args = ap.parse_args()

    mapping = load_map(Path(args.map))
    original = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    if not isinstance(original, dict):
        raise SystemExit("Workflow root must be a JSON object")

    if args.dry_run:
        data = copy.deepcopy(original)
    else:
        data = original

    replaced, unmapped = migrate_workflow(data, mapping)

    print(f"Type renames applied: {replaced}", file=sys.stderr)
    if args.list_unmapped:
        print("Distinct node types with no mapping rule (add to workflow_node_id_map.yaml if renamed):", file=sys.stderr)
        for t in unmapped:
            print(f"  {t}", file=sys.stderr)

    if args.dry_run:
        return 0

    out = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    elif not args.dry_run:
        sys.stdout.write(out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
