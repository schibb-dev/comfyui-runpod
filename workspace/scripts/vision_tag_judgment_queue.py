#!/usr/bin/env python3
"""
Build a blind tag-judgment queue from PromptGen tag NDJSON variants.

Writes ``vision_tag_judgment_queue.json`` under the status dir.
"""

from __future__ import annotations

import argparse
import json
import random
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from vision_slice_review import VARIANT_PREFIX, VARIANT_SUFFIX, load_slice_rows, sanitize_variant_id
from vision_tag_judgment_tags import sample_id_for, tags_from_row

SCHEMA_VERSION = 1
QUEUE_NAME = "vision_tag_judgment_queue.json"

DEFAULT_VARIANTS = [
    "cohort_x2_pg_tags",
    "cohort_x2_pg_large_tags",
    "cohort_pg_tags",
    "cohort_pg_large_tags",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _files_url(rel: str) -> str:
    s = str(rel or "").replace("\\", "/").strip().lstrip("/")
    return "/files/" + urllib.parse.quote(s, safe="/")


def _slice_key(row: Dict[str, Any]) -> Tuple[str, float, float, str]:
    return (
        str(row.get("asset_relpath") or ""),
        float(row.get("t0") or 0.0),
        float(row.get("t1") or 0.0),
        str(row.get("slice") or "window"),
    )


def load_queue(status_dir: Path) -> Dict[str, Any]:
    path = Path(status_dir) / QUEUE_NAME
    if not path.is_file():
        return {"schema": SCHEMA_VERSION, "items": [], "item_count": 0}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": SCHEMA_VERSION, "items": [], "item_count": 0}
    return doc if isinstance(doc, dict) else {"schema": SCHEMA_VERSION, "items": [], "item_count": 0}


def build_judgment_queue(
    status_dir: Path,
    *,
    variant_ids: Sequence[str] = DEFAULT_VARIANTS,
    target_samples: int = 48,
    seed: int = 20260716,
) -> Dict[str, Any]:
    status_dir = Path(status_dir)
    # key -> variant_id -> tags
    by_key: Dict[Tuple[str, float, float, str], Dict[str, List[str]]] = defaultdict(dict)
    meta_by_key: Dict[Tuple[str, float, float, str], Dict[str, Any]] = {}

    used_variants: List[str] = []
    for vid in variant_ids:
        sid = sanitize_variant_id(vid)
        path = status_dir / f"{VARIANT_PREFIX}{sid}{VARIANT_SUFFIX}"
        rows = load_slice_rows(path)
        if not rows:
            continue
        used_variants.append(sid)
        for r in rows:
            key = _slice_key(r)
            if not key[0]:
                continue
            tags = tags_from_row(r)
            if not tags:
                continue
            by_key[key][sid] = tags
            if key not in meta_by_key:
                meta_by_key[key] = {
                    "asset_relpath": key[0],
                    "t0": key[1],
                    "t1": key[2],
                    "slice": key[3],
                    "frame_t": r.get("frame_t"),
                    "frame_relpath": r.get("frame_relpath"),
                    "excerpt_index": r.get("excerpt_index"),
                    "excerpt_video_relpath": r.get("excerpt_video_relpath"),
                    "excerpt_local_t": r.get("excerpt_local_t"),
                }

    # Eligible: ≥2 tag variants on the same slice key.
    candidates: List[Tuple[str, float, float, str]] = [
        k for k, vm in by_key.items() if len(vm) >= 2
    ]

    # Stratify: prefer one window per asset first.
    by_asset: Dict[str, List[Tuple[str, float, float, str]]] = defaultdict(list)
    for k in candidates:
        if k[3] != "window":
            continue
        by_asset[k[0]].append(k)
    rng = random.Random(seed)
    primary: List[Tuple[str, float, float, str]] = []
    for asset, keys in sorted(by_asset.items()):
        rng.shuffle(keys)
        primary.append(keys[0])
    rng.shuffle(primary)

    extras = [k for k in candidates if k not in set(primary)]
    rng.shuffle(extras)
    selected = (primary + extras)[: max(1, int(target_samples))]

    items: List[Dict[str, Any]] = []
    for key in selected:
        vm = by_key[key]
        meta = meta_by_key[key]
        union: Set[str] = set()
        emitted_by: Dict[str, List[str]] = {}
        for vid, tags in vm.items():
            for t in tags:
                union.add(t)
                emitted_by.setdefault(t, []).append(vid)
        for t in emitted_by:
            emitted_by[t] = sorted(set(emitted_by[t]))
        tags_sorted = sorted(union)
        ex_rel = meta.get("excerpt_video_relpath")
        frame_rel = meta.get("frame_relpath")
        items.append(
            {
                "sample_id": sample_id_for(
                    asset_relpath=key[0], t0=key[1], t1=key[2], slice_name=key[3]
                ),
                "asset_relpath": key[0],
                "basename": Path(key[0].replace("\\", "/")).name,
                "t0": key[1],
                "t1": key[2],
                "frame_t": meta.get("frame_t"),
                "slice": key[3],
                "excerpt_index": meta.get("excerpt_index"),
                "excerpt_local_t": meta.get("excerpt_local_t"),
                "video_url": _files_url(key[0]),
                "excerpt_video_url": _files_url(str(ex_rel)) if ex_rel else None,
                "frame_url": _files_url(str(frame_rel)) if frame_rel else None,
                "tags": tags_sorted,
                "emitted_by": emitted_by,
                "variant_ids": sorted(vm.keys()),
            }
        )

    doc = {
        "schema": SCHEMA_VERSION,
        "built_utc": utc_now(),
        "seed": seed,
        "target_samples": target_samples,
        "variants": used_variants,
        "candidate_count": len(candidates),
        "item_count": len(items),
        "items": items,
        "note": "Blind tag judgment experiment — model ids in emitted_by are for scoring only.",
    }
    out_path = status_dir / QUEUE_NAME
    out_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    doc["_path"] = str(out_path)
    return doc


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build vision tag judgment queue")
    ap.add_argument(
        "--status-dir",
        type=Path,
        default=Path("/home/yuji/comfyui-runpod-data/output/_status"),
    )
    ap.add_argument("--target-samples", type=int, default=48)
    ap.add_argument("--seed", type=int, default=20260716)
    ap.add_argument(
        "--variants",
        nargs="*",
        default=DEFAULT_VARIANTS,
        help="Variant ids to compete (default: cohort_x2 + cohort PromptGen tags)",
    )
    args = ap.parse_args(list(argv) if argv is not None else None)
    doc = build_judgment_queue(
        Path(args.status_dir),
        variant_ids=list(args.variants or DEFAULT_VARIANTS),
        target_samples=int(args.target_samples),
        seed=int(args.seed),
    )
    print(
        json.dumps(
            {
                "ok": True,
                "path": doc.get("_path"),
                "item_count": doc.get("item_count"),
                "candidate_count": doc.get("candidate_count"),
                "variants": doc.get("variants"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
