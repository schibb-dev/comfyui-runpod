"""
Validate GEX2 ComfyUI workflows and normalize `nodes` array order to match
FB9_GEX2_COLORv2.json (canonical). All GEX2 variants share the same link graph;
out-of-order node lists can make the UI look like duplicated stacks.

Run from repo root: python workspace/scripts/validate_and_normalize_gex2_workflows.py
"""
from __future__ import annotations

import copy
import json
import sys
from collections import Counter
from pathlib import Path

WF_DIR = Path(__file__).resolve().parents[1] / "comfyui_user" / "default" / "workflows"
REF_NAME = "FB9_GEX2_COLORv2.json"


def validate(d: dict, ref_ids: list[int], ref_link_tuples: list[tuple], name: str) -> list[str]:
    issues: list[str] = []
    if not isinstance(d, dict):
        return [f"{name}: root is not an object"]
    nodes = d.get("nodes") or []
    links = d.get("links") or []
    ids = [n.get("id") for n in nodes]
    if len(ids) != len(set(ids)):
        c = Counter(ids)
        issues.append(f"{name}: duplicate node id(s): {[i for i, n in c.items() if n > 1]}")
    if set(ids) != set(ref_ids):
        issues.append(f"{name}: node id set differs from {REF_NAME}")
    if Counter(tuple(L) for L in links) != Counter(ref_link_tuples):
        issues.append(f"{name}: link multiset differs from {REF_NAME}")
    # connectivity
    node_set = set(ids)
    adj = {i: set() for i in node_set}
    for L in links:
        if len(L) < 5:
            continue
        _, a, _, b, _, *_ = L
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
    incoming = {i: 0 for i in node_set}
    for L in links:
        if len(L) < 5:
            continue
        _, _, _, tn, _, *_ = L
        if tn in incoming:
            incoming[tn] += 1
    roots = [i for i, n in incoming.items() if n == 0]
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        for v in adj[u]:
            if v not in seen:
                stack.append(v)
    if seen != node_set:
        issues.append(f"{name}: disconnected nodes {sorted(node_set - seen)}")
    # singleton loaders
    types = Counter(n.get("type") for n in nodes)
    for t in ("UnetLoaderGGUFDisTorchMultiGPU", "VAELoader", "WanImageToVideo", "VHS_LoadVideoPath"):
        if types.get(t, 0) != 1:
            issues.append(f"{name}: expected exactly one {t}, found {types.get(t, 0)}")
    return issues


def main() -> int:
    ref_path = WF_DIR / REF_NAME
    if not ref_path.exists():
        print("Missing reference:", ref_path, file=sys.stderr)
        return 1
    ref = json.loads(ref_path.read_text(encoding="utf-8"))
    ref_order = [n["id"] for n in ref["nodes"]]
    ref_ids = ref_order
    ref_link_tuples = [tuple(L) for L in ref["links"]]
    ref_links = ref["links"]

    gex2 = sorted(WF_DIR.glob("*GEX2*.json"))
    if not gex2:
        print("No GEX2 workflows under", WF_DIR)
        return 0

    all_issues: list[str] = []
    changed: list[str] = []

    for path in gex2:
        d = json.loads(path.read_text(encoding="utf-8"))
        name = path.name
        issues = validate(d, ref_ids, ref_link_tuples, name)
        all_issues.extend(issues)

        blocked = any(
            s in msg
            for msg in issues
            for s in (
                "duplicate node id",
                "node id set differs",
                "link multiset differs",
                "disconnected nodes",
                "cannot normalize",
            )
        )
        if blocked:
            continue

        by_id = {n["id"]: n for n in d["nodes"]}
        if set(by_id) != set(ref_order):
            all_issues.append(f"{name}: cannot normalize — missing/extra ids vs reference")
            continue

        modified = False
        cur_order = [n["id"] for n in d["nodes"]]
        if cur_order != ref_order:
            d["nodes"] = [copy.deepcopy(by_id[i]) for i in ref_order]
            modified = True
        if d.get("links") != ref_links:
            d["links"] = copy.deepcopy(ref_links)
            modified = True
        if modified:
            path.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")
            changed.append(name)

    if all_issues:
        print("Validation issues:")
        for i in all_issues:
            print(" ", i)
    else:
        print("Validation: OK for", len(gex2), "GEX2 workflows (topology matches", REF_NAME + ").")

    if changed:
        print("Normalized node/link ordering:", ", ".join(changed))
    else:
        print("Node order: already canonical.")

    return 1 if all_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
