#!/usr/bin/env python3
"""Remove delivery postprocess nodes from factory catalog LiteGraph workflows.

See docs/WORKFLOW_LAYERS.md Phase 1.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Set

SEED_NODE_TYPES = frozenset({"UpscaleModelLoader", "ImageUpscaleWithModel", "RIFE VFI"})
POSTPROCESS_DELIVERY_TYPES = frozenset({"ImageScaleBy"})
BYPASSER_MATCH_TITLES = frozenset({"Upscaler", "Interpolation"})


def _is_delivery_bypasser(node: dict[str, Any]) -> bool:
    if str(node.get("type") or "") != "Fast Groups Bypasser (rgthree)":
        return False
    props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    return str(props.get("matchTitle") or "") in BYPASSER_MATCH_TITLES


def _seed_origin_node_ids(nodes: list[dict[str, Any]]) -> Set[int]:
    removed: Set[int] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if nid is None:
            continue
        ntype = str(node.get("type") or "")
        title = str(node.get("title") or "")
        if ntype in SEED_NODE_TYPES or _is_delivery_bypasser(node):
            removed.add(int(nid))
            continue
        if title.startswith("POSTPROCESS:") and ntype in POSTPROCESS_DELIVERY_TYPES:
            removed.add(int(nid))
    return removed


def _collect_removed_link_ids(links: list[Any], removed_node_ids: Set[int]) -> Set[int]:
    removed: Set[int] = set()
    for item in links:
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            continue
        if item[1] in removed_node_ids or item[3] in removed_node_ids:
            removed.add(int(item[0]))
    return removed


def _find_spur_node_ids(
    nodes: list[dict[str, Any]],
    links: list[Any],
    removed_node_ids: Set[int],
) -> Set[int]:
    link_src: dict[int, int] = {}
    for item in links:
        if isinstance(item, (list, tuple)) and len(item) >= 5:
            link_src[int(item[0])] = int(item[1])

    spur_ids: Set[int] = set()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if nid is None or int(nid) in removed_node_ids:
            continue
        input_links = [
            inp.get("link")
            for inp in node.get("inputs") or []
            if isinstance(inp, dict) and inp.get("link") is not None
        ]
        if not input_links:
            continue
        source_nodes = {link_src[int(lid)] for lid in input_links if int(lid) in link_src}
        if source_nodes and all(s in removed_node_ids for s in source_nodes):
            spur_ids.add(int(nid))
    return spur_ids


def _clear_links_in_nodes(nodes: list[dict[str, Any]], removed_link_ids: Set[int]) -> None:
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for inp in node.get("inputs") or []:
            if isinstance(inp, dict) and inp.get("link") in removed_link_ids:
                inp["link"] = None
        for out in node.get("outputs") or []:
            if not isinstance(out, dict):
                continue
            links = out.get("links")
            if isinstance(links, list):
                out["links"] = [x for x in links if x not in removed_link_ids]


def detect_mode(workflow: dict[str, Any]) -> str:
    types = {str(n.get("type") or "") for n in workflow.get("nodes") or []}
    if "ImageUpscaleWithModel" in types or "RIFE VFI" in types:
        return "origin"
    return "extend"


def strip_delivery_postprocess(
    data: dict[str, Any],
    *,
    mode: str | None = None,
) -> tuple[dict[str, Any], Set[int]]:
    """Return (workflow, removed_node_ids). mode: origin | extend | auto-detect."""
    out = json.loads(json.dumps(data))
    nodes: list[dict[str, Any]] = list(out.get("nodes") or [])
    links: list[Any] = list(out.get("links") or [])
    mode = mode or detect_mode(out)

    if mode == "extend":
        removed_node_ids = {
            int(n["id"])
            for n in nodes
            if isinstance(n, dict) and n.get("id") is not None and _is_delivery_bypasser(n)
        }
    elif mode == "origin":
        removed_node_ids = _seed_origin_node_ids(nodes)
        while True:
            spur_ids = _find_spur_node_ids(nodes, links, removed_node_ids)
            if not spur_ids:
                break
            removed_node_ids |= spur_ids
    else:
        raise ValueError(f"unknown mode: {mode}")

    if not removed_node_ids:
        return out, set()

    removed_link_ids = _collect_removed_link_ids(links, removed_node_ids)
    out["nodes"] = [
        n for n in nodes if isinstance(n, dict) and int(n.get("id", -1)) not in removed_node_ids
    ]
    out["links"] = [
        link
        for link in links
        if isinstance(link, (list, tuple))
        and len(link) >= 5
        and int(link[0]) not in removed_link_ids
    ]
    _clear_links_in_nodes(out["nodes"], removed_link_ids)
    return out, removed_node_ids


def graph_hash_for_workflow(workflow: dict[str, Any]) -> str:
    from shape_factory_vocab import graph_fingerprint_topology

    return graph_fingerprint_topology(workflow, aliases=False)


def apply_to_path(path: Path, *, mode: str | None = None, backup: bool = True, dry_run: bool = False) -> dict[str, Any]:
    wf = json.loads(path.read_text(encoding="utf-8"))
    effective_mode = mode or detect_mode(wf)
    stripped, removed = strip_delivery_postprocess(wf, mode=effective_mode)
    old_hash = graph_hash_for_workflow(wf) if wf.get("nodes") else None
    result: dict[str, Any] = {
        "path": str(path),
        "mode": effective_mode,
        "removed_nodes": len(removed),
        "changed": bool(removed),
        "old_graph_hash": old_hash,
    }
    if removed:
        result["new_graph_hash"] = graph_hash_for_workflow(stripped)
    if dry_run or not removed:
        return result
    if backup:
        bak = path.with_suffix(path.suffix + ".pre-delivery-strip.bak")
        if not bak.is_file():
            shutil.copy2(path, bak)
        result["backup"] = str(bak)
    path.write_text(json.dumps(stripped, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Strip delivery postprocess from catalog workflows")
    ap.add_argument("workflow", nargs="?", type=Path, help="Single workflow JSON")
    ap.add_argument("--catalog-dir", type=Path, help="Apply to all *-readable.json in directory")
    ap.add_argument("--mode", choices=["origin", "extend"])
    ap.add_argument("--apply", action="store_true", help="Write changes")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    dry_run = not args.apply
    if args.catalog_dir:
        catalog = args.catalog_dir.expanduser().resolve()
        for path in sorted(catalog.glob("*-readable.json")):
            res = apply_to_path(
                path,
                mode=args.mode,
                backup=not args.no_backup,
                dry_run=dry_run,
            )
            print(json.dumps(res))
        return 0

    if not args.workflow:
        ap.error("workflow path or --catalog-dir required")
        return 2

    res = apply_to_path(
        args.workflow.expanduser().resolve(),
        mode=args.mode,
        backup=not args.no_backup,
        dry_run=dry_run,
    )
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
