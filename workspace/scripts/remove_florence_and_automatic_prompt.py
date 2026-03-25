#!/usr/bin/env python3
"""
Remove Florence (Florence2) nodes and the "automatic prompt" group from ComfyUI workflow JSON.

Useful when a workflow was built with ComfyUI-Florence2 for automatic captioning and you want
a version that relies only on manual prompts. This script:

  1. Removes all Florence-related nodes (DownloadAndLoadFlorence2Model, Florence2Run, or any
     node whose type/properties reference Florence).
  2. Removes "spur" nodes that only carried Florence output into the graph (e.g. a Text Find
     and Replace whose only input was the Florence caption); removes their links too.
  3. Removes every link that starts or ends at a removed node.
  4. Cleans remaining nodes: sets input links to null when the link was removed; removes
     removed link ids from output link lists.
  5. Removes the group whose title is "automatic prompt" (case-insensitive).

Usage:
  python remove_florence_and_automatic_prompt.py workflow.json -o workflow_no_florence.json
  python remove_florence_and_automatic_prompt.py workflow.json --in-place
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


def _is_florence_node(node: Dict[str, Any]) -> bool:
    """True if this node is a Florence2 / automatic-prompt node."""
    if not isinstance(node, dict):
        return False
    ntype = (node.get("type") or "").strip()
    if "Florence" in ntype or "florence" in ntype.lower():
        return True
    props = node.get("properties") or {}
    if not isinstance(props, dict):
        return False
    aux = (props.get("aux_id") or "").lower()
    cnr = (props.get("cnr_id") or "").lower()
    if "florence" in aux or "florence" in cnr:
        return True
    return False


def _group_is_automatic_prompt(group: Dict[str, Any]) -> bool:
    """True if group title is 'automatic prompt' (case-insensitive)."""
    if not isinstance(group, dict):
        return False
    title = (group.get("title") or "").strip().lower()
    return title == "automatic prompt"


def _collect_removed_link_ids(links: List[Any], removed_node_ids: Set[int]) -> Set[int]:
    """Return set of link ids for links that touch a removed node. Link format: [id, src, src_slot, tgt, tgt_slot, type]."""
    removed: Set[int] = set()
    for item in links:
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            continue
        link_id = item[0]
        src_node = item[1]
        tgt_node = item[3]
        if src_node in removed_node_ids or tgt_node in removed_node_ids:
            removed.add(link_id)
    return removed


def _find_spur_node_ids(
    nodes: List[Dict[str, Any]],
    links: List[Any],
    removed_node_ids: Set[int],
) -> Set[int]:
    """
    Nodes that only receive inputs from already-removed nodes (the "spur" that inserted
    Florence output into the graph). Such nodes are removed so the spur disappears.
    """
    link_src: Dict[int, int] = {}
    for item in links:
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            continue
        link_src[item[0]] = item[1]

    spur_ids: Set[int] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id")
        if nid is None or nid in removed_node_ids:
            continue
        input_links = []
        for inp in n.get("inputs") or []:
            if isinstance(inp, dict) and inp.get("link") is not None:
                input_links.append(inp["link"])
        if not input_links:
            continue
        source_nodes = {link_src[lid] for lid in input_links if lid in link_src}
        if source_nodes and all(s in removed_node_ids for s in source_nodes):
            spur_ids.add(int(nid))
    return spur_ids


def _clear_links_in_nodes(nodes: List[Dict[str, Any]], removed_link_ids: Set[int]) -> None:
    """Mutate nodes: clear input links and output link lists that reference removed_link_ids."""
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


def remove_florence_and_automatic_prompt(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a new workflow dict with Florence nodes, their links, and the automatic_prompt group removed.
    """
    data = json.loads(json.dumps(data))  # deep copy

    nodes: List[Dict[str, Any]] = list(data.get("nodes") or [])
    links: List[Any] = list(data.get("links") or [])
    groups: List[Dict[str, Any]] = list(data.get("groups") or [])

    # 1) Node ids to remove (Florence)
    removed_node_ids: Set[int] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = n.get("id")
        if nid is not None and _is_florence_node(n):
            removed_node_ids.add(int(nid))

    # 2) Iteratively add "spur" nodes that only receive from removed nodes (e.g. Text Find and Replace fed by Florence caption)
    while True:
        spur_ids = _find_spur_node_ids(nodes, links, removed_node_ids)
        if not spur_ids:
            break
        removed_node_ids |= spur_ids

    # 3) Link ids to remove (links touching any removed node)
    removed_link_ids = _collect_removed_link_ids(links, removed_node_ids)

    # 4) Remove removed nodes and links
    data["nodes"] = [n for n in nodes if isinstance(n, dict) and n.get("id") not in removed_node_ids]

    data["links"] = [
        L for L in links
        if isinstance(L, (list, tuple)) and len(L) >= 5 and L[0] not in removed_link_ids
    ]

    # 5) Clear references to removed links in remaining nodes
    _clear_links_in_nodes(data["nodes"], removed_link_ids)

    # 6) Remove "automatic prompt" group(s)
    data["groups"] = [g for g in groups if not _group_is_automatic_prompt(g)]

    return data


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Remove Florence nodes and automatic_prompt group from ComfyUI workflow JSON",
    )
    ap.add_argument("workflow", type=Path, help="Input workflow JSON path")
    ap.add_argument("-o", "--output", type=Path, default=None, help="Output path (default: print to stdout)")
    ap.add_argument("--in-place", action="store_true", help="Overwrite input file (ignores -o)")
    ap.add_argument("--indent", type=int, default=2, help="JSON indent (default: 2)")
    args = ap.parse_args()

    path = args.workflow.resolve()
    if not path.exists() or not path.is_file():
        print(f"Error: not a file: {path}", file=sys.stderr)
        return 1

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading JSON: {e}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print("Error: workflow JSON must be an object", file=sys.stderr)
        return 1

    out_data = remove_florence_and_automatic_prompt(data)

    if args.in_place:
        out_path = path
    elif args.output is not None:
        out_path = args.output.resolve()
    else:
        out_path = None

    try:
        if out_path is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(out_data, f, indent=args.indent, ensure_ascii=False)
            print(f"Wrote: {out_path}", file=sys.stderr)
        else:
            json.dump(out_data, sys.stdout, indent=args.indent, ensure_ascii=False)
    except Exception as e:
        print(f"Error writing: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
