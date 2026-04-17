"""
ComfyUI **litegraph** workflow JSON (top-level ``nodes`` + ``links``): cheap graph checks.

``links`` rows are ``[link_id, from_node_id, from_slot, to_node_id, to_slot, type?]`` as in Comfy exports.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def litegraph_linked_node_ids(links: list[Any]) -> set[int]:
    """Node ids that appear as either end of any link row."""
    ids: set[int] = set()
    for row in links or []:
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        try:
            ids.add(int(row[1]))
            ids.add(int(row[3]))
        except (TypeError, ValueError):
            continue
    return ids


def disconnected_litegraph_nodes(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Nodes that never appear as a link endpoint (in-degree and out-degree 0 in the link table).

    Does not inspect per-node ``outputs[].links`` — the global ``links`` array is the source of truth
    for exports checked here.
    """
    nodes = workflow.get("nodes") or []
    links = workflow.get("links") or []
    linked = litegraph_linked_node_ids(links)
    out: list[dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict) or "id" not in n:
            continue
        try:
            nid = int(n["id"])
        except (TypeError, ValueError):
            continue
        if nid in linked:
            continue
        out.append(
            {
                "id": nid,
                "type": str(n.get("type") or ""),
                "title": str(n.get("title") or ""),
                "mode": n.get("mode"),
            }
        )
    out.sort(key=lambda r: r["id"])
    return out


# Nodes that are usually meaningful if wired; disconnected ones are often mistakes (e.g. stray KSampler).
WARN_IF_DISCONNECTED_TYPES: frozenset[str] = frozenset(
    {
        "KSampler",
        "KSamplerAdvanced",
        "RandomNoise",
        "EmptyLatentImage",
        "CLIPTextEncode",
        "Sampler",
        "SamplerCustom",
        "SamplerCustomAdvanced",
    }
)

# Often harmless when floating (documentation / UI-only).
LOW_PRIORITY_DISCONNECTED_TYPES: frozenset[str] = frozenset(
    {
        "Note",
        "MarkdownNote",
    }
)


def classify_disconnected_nodes(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (warn, low_priority, other)."""
    warn: list[dict[str, Any]] = []
    low: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for r in rows:
        t = r["type"]
        if t in WARN_IF_DISCONNECTED_TYPES:
            warn.append(r)
        elif t in LOW_PRIORITY_DISCONNECTED_TYPES:
            low.append(r)
        else:
            other.append(r)
    return warn, low, other


def format_disconnected_report(rows: list[dict[str, Any]], *, indent: str = "  ") -> list[str]:
    lines: list[str] = []
    if not rows:
        return lines
    warn, low, other = classify_disconnected_nodes(rows)
    if warn:
        lines.append(f"{indent}**Warn (likely spurious):**")
        for r in warn:
            mode = r.get("mode")
            mo = f" mode={mode}" if mode not in (None, 0) else ""
            title = (r.get("title") or "").strip()
            tt = f' "{title}"' if title else ""
            lines.append(f"{indent}- [n{r['id']}] {r['type']}{tt}{mo}")
    if other:
        lines.append(f"{indent}Other disconnected:")
        for r in other:
            mode = r.get("mode")
            mo = f" mode={mode}" if mode not in (None, 0) else ""
            title = (r.get("title") or "").strip()
            tt = f' "{title}"' if title else ""
            lines.append(f"{indent}- [n{r['id']}] {r['type']}{tt}{mo}")
    if low:
        lines.append(f"{indent}Low priority (often intentional):")
        for r in low:
            lines.append(f"{indent}- [n{r['id']}] {r['type']}")
    return lines


def load_workflow_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scan_file(path: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Returns (disconnected rows or None on error, error message)."""
    try:
        data = load_workflow_json(path)
    except OSError as e:
        return None, str(e)
    except json.JSONDecodeError as e:
        return None, str(e)
    if not isinstance(data, dict):
        return None, "root is not an object"
    rows = disconnected_litegraph_nodes(data)
    return rows, None
