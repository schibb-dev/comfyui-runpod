#!/usr/bin/env python3
"""
Check that every `nodes[].type` in a ComfyUI UI workflow JSON exists in a running ComfyUI's
registered node list (`GET /object_info`).

Usage:
  python verify_workflow_node_types.py --workflow /path/to/wf.json --server http://127.0.0.1:8188

Optional: compare against a saved snapshot instead of live server:
  python verify_workflow_node_types.py --workflow wf.json --object-info-json snapshot.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Set

_HERE = Path(__file__).resolve()
for _cand in (_HERE.parent, _HERE.parents[1] / "workspace" / "scripts"):
    try:
        if _cand.is_dir() and str(_cand) not in sys.path:
            sys.path.insert(0, str(_cand))
    except Exception:
        continue
from http_retry import http_json_with_retry


def collect_types(workflow: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return out
    for n in nodes:
        if isinstance(n, dict) and isinstance(n.get("type"), str):
            out.add(n["type"])
    return out


def fetch_object_info(server: str) -> Dict[str, Any]:
    url = server.rstrip("/") + "/object_info"
    out = http_json_with_retry(method="GET", url=url, timeout_s=60)
    return out if isinstance(out, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workflow", "-w", required=True, help="UI workflow JSON path")
    ap.add_argument("--server", "-s", default=os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188"))
    ap.add_argument("--object-info-json", help="Use this file instead of GET /object_info")
    args = ap.parse_args()

    wf = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    types = collect_types(wf)

    if args.object_info_json:
        obj_info = json.loads(Path(args.object_info_json).read_text(encoding="utf-8"))
    else:
        try:
            obj_info = fetch_object_info(args.server)
        except Exception as e:
            print(f"Failed to fetch {args.server}/object_info: {e}", file=sys.stderr)
            return 2

    registered = set(obj_info.keys()) if isinstance(obj_info, dict) else set()
    missing = sorted(t for t in types if t not in registered)

    print(f"Workflow types: {len(types)}", file=sys.stderr)
    print(f"Registered node classes (object_info keys): {len(registered)}", file=sys.stderr)
    if not missing:
        print("OK: all workflow node types are registered.", file=sys.stderr)
        return 0

    print("MISSING (not in object_info):", file=sys.stderr)
    for t in missing:
        print(f"  {t}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
