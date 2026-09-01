#!/usr/bin/env python3
"""Exhaustive oldest→newest inventory: og PNG embeds vs enrolled + catalog templates.

Writes:
  .data/shape_factory/og_template_match_inventory.json   (final)
  .data/shape_factory/og_template_match_progress.json    (running tally)

Prints a tally at least every ``--tally-seconds`` (default 300).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from comfy_meta_lib import extract_prompt_workflow_from_png_chunks, read_png_text_chunks
from shape_factory import load_yaml
from shape_factory_vocab import graph_fingerprint_topology, load_workflow_json

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OG = Path("/home/yuji/comfyui-runpod-data/output/og")
DEFAULT_SHAPES = REPO / ".data" / "shapes"
DEFAULT_CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog"
)
DEFAULT_OUT = REPO / ".data" / "shape_factory" / "og_template_match_inventory.json"
DEFAULT_PROGRESS = REPO / ".data" / "shape_factory" / "og_template_match_progress.json"
DATE_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


def _utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enrolled_fps(shapes_dir: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(shapes_dir.glob("*.shape.yaml")):
        doc = load_yaml(path)
        slug = str(doc.get("family_slug") or path.name[: -len(".shape.yaml")])
        meta = {"family_slug": slug, "shape_path": str(path)}
        gh = str(doc.get("graph_hash") or "").strip()
        if gh:
            out[gh] = {**meta, "via": "graph_hash"}
        tpl = doc.get("template")
        if tpl:
            wf = load_workflow_json(Path(str(tpl)))
            if wf and isinstance(wf.get("nodes"), list):
                fp = graph_fingerprint_topology(wf)
                out.setdefault(fp, {**meta, "via": "template_topology"})
    return out


def catalog_fps(catalog_dir: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not catalog_dir.is_dir():
        return out
    for path in sorted(catalog_dir.glob("*-readable.json")):
        wf = load_workflow_json(path)
        if not wf or not isinstance(wf.get("nodes"), list):
            continue
        fp = graph_fingerprint_topology(wf)
        out.setdefault(fp, {"name": path.name, "path": str(path), "via": "topology"})
    return out


def png_fp(png: Path) -> Tuple[Optional[str], Optional[str]]:
    try:
        chunks = read_png_text_chunks(png)
        _pr, wf = extract_prompt_workflow_from_png_chunks(chunks)
    except Exception as e:
        return None, f"read_err:{e}"
    if not isinstance(wf, dict) or not isinstance(wf.get("nodes"), list):
        return None, "no_workflow"
    try:
        return graph_fingerprint_topology(wf), None
    except Exception as e:
        return None, f"fp_err:{e}"


def _agg(by_fp: Dict[str, Dict[str, Any]]) -> Tuple[Counter, Counter]:
    fps = Counter()
    videos = Counter()
    for b in by_fp.values():
        fps[b["match"]] += 1
        videos[b["match"]] += int(b.get("with_mp4") or 0)
    return fps, videos


def write_progress(
    path: Path,
    *,
    stats: Counter,
    by_fp: Dict[str, Dict[str, Any]],
    date_dirs: List[Path],
    di: int,
    started: float,
    status: str = "running",
) -> Dict[str, Any]:
    fps_c, vid_c = _agg(by_fp)
    day = date_dirs[di].name if 0 <= di < len(date_dirs) else None
    elapsed = max(0.0, time.time() - started)
    payload = {
        "status": status,
        "updated_at": _utc(),
        "elapsed_sec": round(elapsed, 1),
        "day_index": di + 1,
        "day_total": len(date_dirs),
        "current_date": day,
        "pct_days": round(100.0 * (di + 1) / max(1, len(date_dirs)), 1),
        "stats": dict(stats),
        "unique_fingerprints": {
            "total": len(by_fp),
            "enrolled": fps_c["enrolled"],
            "catalog_only": fps_c["catalog_only"],
            "unmatched": fps_c["unmatched"],
        },
        "videos_with_mp4_by_match": {
            "enrolled": vid_c["enrolled"],
            "catalog_only": vid_c["catalog_only"],
            "unmatched": vid_c["unmatched"],
            "total": sum(vid_c.values()),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def print_tally(payload: Dict[str, Any]) -> None:
    st = payload.get("stats") or {}
    uf = payload.get("unique_fingerprints") or {}
    vv = payload.get("videos_with_mp4_by_match") or {}
    total_v = max(1, int(vv.get("total") or 0))
    unmatched_pct = 100.0 * int(vv.get("unmatched") or 0) / total_v if vv.get("total") else 0.0
    line = (
        f"[tally {payload.get('updated_at')}] "
        f"day {payload.get('day_index')}/{payload.get('day_total')} "
        f"({payload.get('pct_days')}%) @ {payload.get('current_date')} | "
        f"png={st.get('png_seen', 0)} fp={st.get('fingerprinted', 0)} "
        f"mp4={st.get('png_with_mp4', 0)} | "
        f"fps enrolled={uf.get('enrolled', 0)} catalog={uf.get('catalog_only', 0)} "
        f"unmatched={uf.get('unmatched', 0)} | "
        f"videos enrolled={vv.get('enrolled', 0)} catalog={vv.get('catalog_only', 0)} "
        f"unmatched={vv.get('unmatched', 0)} ({unmatched_pct:.1f}%) | "
        f"elapsed={payload.get('elapsed_sec')}s"
    )
    print(line, flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--og-root", type=Path, default=DEFAULT_OG)
    ap.add_argument("--shapes-dir", type=Path, default=DEFAULT_SHAPES)
    ap.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    ap.add_argument("--tally-seconds", type=float, default=300.0)
    args = ap.parse_args(argv)

    print("Loading enrolled + catalog fingerprints…", flush=True)
    enrolled = enrolled_fps(args.shapes_dir)
    catalog = catalog_fps(args.catalog_dir)
    print(f"  enrolled fps: {len(enrolled)}  catalog fps: {len(catalog)}", flush=True)

    og = args.og_root
    date_dirs = sorted(
        [p for p in og.iterdir() if p.is_dir() and DATE_RE.match(p.name) and not p.name.startswith("_")],
        key=lambda p: p.name,
    )
    print(
        f"Scanning {len(date_dirs)} date dirs under {og} "
        f"(oldest→newest); tally every {args.tally_seconds:.0f}s…",
        flush=True,
    )

    stats: Counter = Counter()
    by_fp: Dict[str, Dict[str, Any]] = {}
    unmatched_examples: List[Dict[str, Any]] = []
    started = time.time()
    last_tally = started

    for di, day in enumerate(date_dirs):
        pngs: List[Path] = []
        pngs.extend(day.glob("*.png"))
        hourly = day / "hourly"
        if hourly.is_dir():
            pngs.extend(hourly.glob("*.png"))
        pngs.sort(key=lambda p: p.stat().st_mtime)

        for png in pngs:
            stats["png_seen"] += 1
            mp4 = png.with_suffix(".mp4")
            has_mp4 = mp4.is_file()
            if has_mp4:
                stats["png_with_mp4"] += 1
            else:
                stats["png_orphan"] += 1

            fp, err = png_fp(png)
            if err:
                key = err.split(":", 1)[0] if ":" in err else err
                stats[key] += 1
                continue
            stats["fingerprinted"] += 1

            bucket = by_fp.get(fp)
            if bucket is None:
                if fp in enrolled:
                    kind = "enrolled"
                    label = enrolled[fp]["family_slug"]
                elif fp in catalog:
                    kind = "catalog_only"
                    label = catalog[fp]["name"]
                else:
                    kind = "unmatched"
                    label = None
                bucket = {
                    "fingerprint": fp,
                    "match": kind,
                    "label": label,
                    "count": 0,
                    "with_mp4": 0,
                    "first_date": day.name,
                    "last_date": day.name,
                    "example_png": str(png),
                    "example_mp4": str(mp4) if has_mp4 else None,
                }
                by_fp[fp] = bucket
                if kind == "unmatched" and len(unmatched_examples) < 40:
                    unmatched_examples.append(
                        {
                            "date": day.name,
                            "png": png.name,
                            "mp4": mp4.name if has_mp4 else None,
                            "fp": fp[:16],
                        }
                    )
            bucket["count"] += 1
            if has_mp4:
                bucket["with_mp4"] += 1
            bucket["last_date"] = day.name

        now = time.time()
        if (now - last_tally) >= float(args.tally_seconds) or di == 0 or di == len(date_dirs) - 1:
            prog = write_progress(
                args.progress,
                stats=stats,
                by_fp=by_fp,
                date_dirs=date_dirs,
                di=di,
                started=started,
            )
            print_tally(prog)
            last_tally = now

    fps_c, vid_c = _agg(by_fp)
    enrolled_vid: Counter = Counter()
    catalog_vid: Counter = Counter()
    for b in by_fp.values():
        if b["match"] == "enrolled":
            enrolled_vid[b["label"]] += int(b["with_mp4"])
        elif b["match"] == "catalog_only":
            catalog_vid[b["label"]] += int(b["with_mp4"])

    payload = {
        "schema_version": "comfyui-runpod.og-template-match-inventory.v1",
        "generated_at": _utc(),
        "og_root": str(og),
        "scan_order": "oldest_to_newest_date_dir_then_mtime",
        "date_range": {
            "first": date_dirs[0].name if date_dirs else None,
            "last": date_dirs[-1].name if date_dirs else None,
            "days": len(date_dirs),
        },
        "reference": {
            "enrolled_fingerprints": len(enrolled),
            "catalog_fingerprints": len(catalog),
            "enrolled_families": sorted({m["family_slug"] for m in enrolled.values()}),
        },
        "stats": dict(stats),
        "unique_fingerprints": {
            "total": len(by_fp),
            "enrolled": fps_c["enrolled"],
            "catalog_only": fps_c["catalog_only"],
            "unmatched": fps_c["unmatched"],
        },
        "videos_with_mp4_by_match": {
            "enrolled": vid_c["enrolled"],
            "catalog_only": vid_c["catalog_only"],
            "unmatched": vid_c["unmatched"],
            "total": sum(vid_c.values()),
        },
        "enrolled_video_counts": dict(enrolled_vid.most_common()),
        "catalog_only_top": catalog_vid.most_common(20),
        "unmatched_top_by_count": sorted(
            (
                {
                    "fingerprint": b["fingerprint"][:24] + "…",
                    "fingerprint_full": b["fingerprint"],
                    "count": b["count"],
                    "with_mp4": b["with_mp4"],
                    "first_date": b["first_date"],
                    "last_date": b["last_date"],
                    "example_mp4": b["example_mp4"],
                    "example_png": b["example_png"],
                }
                for b in by_fp.values()
                if b["match"] == "unmatched"
            ),
            key=lambda r: (-r["with_mp4"], -r["count"], r["first_date"]),
        )[:40],
        "unmatched_earliest_examples": unmatched_examples[:40],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    final_prog = write_progress(
        args.progress,
        stats=stats,
        by_fp=by_fp,
        date_dirs=date_dirs,
        di=max(0, len(date_dirs) - 1),
        started=started,
        status="done",
    )
    print("\n=== SUMMARY ===", flush=True)
    print_tally(final_prog)
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
