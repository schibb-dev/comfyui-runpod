#!/usr/bin/env python3
"""
Asset tagging for tag-coupled discovery.

Tags let appetite ("do more WITH this") generalize by *content* rather than only
by lineage graph or workflow pattern. The store (`asset_tags.json`) is provider-
pluggable and vision-ready:

  provider="prompt"  (bootstrap) — keywords extracted from the embedded positive
                     prompt of shape-factory jobs.
  provider="florence"/"wd14"     — FUTURE: caption/booru taggers over frames. Drop
                     a new `extract_tags_*` provider and add it to PROVIDERS; the
                     rollup (`by_tag_appetite` in heuristics) and consumers (sampler,
                     derive ranking) are provider-agnostic.

Rebuild order: tags build -> ratings build -> heuristics build (heuristics reads tags).
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from shape_factory_heuristics import _og_group_id_from_relpath
from shape_factory_ratings import utc_now

TAGS_SCHEMA_VERSION = 1

# Prompt boilerplate / quality words that carry no discovery signal.
_STOPWORDS = {
    "the", "and", "her", "she", "his", "him", "you", "your", "yours", "with", "onto", "into",
    "from", "that", "this", "which", "them", "they", "are", "was", "were", "for", "out", "off",
    "over", "again", "yet", "even", "more", "most", "some", "any", "all", "who", "how", "why",
    "what", "when", "where", "then", "than", "also", "but", "not", "very", "just", "like", "look",
    "looking", "looks", "camera", "static", "shot", "shots", "scene", "frame", "quality", "best",
    "worst", "high", "low", "detail", "details", "background", "foreground", "style", "styles",
    "starts", "turns", "gets", "getting", "shoots", "shooting", "several", "series", "amount",
    "straight", "front", "side", "left", "right", "wide", "eyed", "man", "woman", "men", "women",
    "one", "two", "three", "her", "she", "his", "its", "a", "an", "of", "in", "on", "is", "it",
    "as", "at", "to", "up", "so", "or", "if", "be", "by", "no",
}
_WORD_RE = re.compile(r"[a-z][a-z0-9]{2,}")


def default_asset_tags_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "asset_tags.json"


def normalize_tag(raw: str) -> str:
    t = str(raw or "").strip().lower()
    t = re.sub(r"[^a-z0-9_]+", "", t)
    return t


def extract_tags_from_prompt(prompt_text: str, *, max_tags: int = 24) -> List[str]:
    """Bootstrap provider: normalized keyword tags from a positive prompt string."""
    text = str(prompt_text or "").lower()
    if not text.strip():
        return []
    counts: Counter[str] = Counter()
    for m in _WORD_RE.findall(text):
        if m in _STOPWORDS or m.isdigit():
            continue
        counts[m] += 1
    # Frequency-desc, then alpha for determinism; keep distinct.
    ordered = [w for w, _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return ordered[: max(1, int(max_tags))]


def _job_output_relpaths(job: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for p in job.get("outputs") or []:
        if p:
            paths.append(str(p))
    submit = job.get("submit")
    if isinstance(submit, dict):
        for p in submit.get("outputs") or []:
            if p:
                paths.append(str(p))
    deposit = job.get("deposit")
    if isinstance(deposit, dict):
        for p in deposit.get("videos") or []:
            if p:
                paths.append(str(p))
    return paths


def _positive_prompt(job: Dict[str, Any]) -> str:
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    pp = bindings.get("prompt_profile") if isinstance(bindings.get("prompt_profile"), dict) else {}
    return str(pp.get("positive") or "")


def build_asset_tags(
    *,
    jobs_root: Path,
    provider: str = "prompt",
    out_path: Optional[Path] = None,
    max_tags: int = 24,
) -> Dict[str, Any]:
    """Build asset_tags.json by group_id from shape-factory job prompts (bootstrap provider)."""
    jobs_root = Path(jobs_root)
    by_group_id: Dict[str, Dict[str, Any]] = {}
    scanned = 0
    if jobs_root.is_dir():
        for job_path in sorted(jobs_root.rglob("*.job.json")):
            try:
                job = json.loads(job_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(job, dict):
                continue
            scanned += 1
            tags = extract_tags_from_prompt(_positive_prompt(job), max_tags=max_tags)
            if not tags:
                continue
            job_key = str(job.get("job_key") or job_path.stem)
            for relpath in _job_output_relpaths(job):
                gid = _og_group_id_from_relpath(relpath)
                if not gid:
                    continue
                # First writer wins per group (jobs are the source of truth for their output).
                by_group_id.setdefault(
                    gid,
                    {"tags": tags, "provider": provider, "source_job": job_key, "updated_at": utc_now()},
                )

    doc = {
        "version": TAGS_SCHEMA_VERSION,
        "provider": provider,
        "updated_at": utc_now(),
        "stats": {"jobs_scanned": scanned, "tagged_groups": len(by_group_id)},
        "by_group_id": by_group_id,
    }
    if out_path:
        out_path = Path(out_path).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def lookup_tags(key: str, tags_doc: Optional[Dict[str, Any]]) -> List[str]:
    """Resolve tags for a group_id or output relpath."""
    if not tags_doc:
        return []
    by_gid = tags_doc.get("by_group_id") or {}
    if not isinstance(by_gid, dict):
        return []
    raw = str(key or "").strip()
    row = by_gid.get(raw)
    if not isinstance(row, dict):
        gid = _og_group_id_from_relpath(raw)
        row = by_gid.get(gid or "")
    if isinstance(row, dict):
        return [str(t) for t in (row.get("tags") or [])]
    return []


PROVIDERS = {"prompt": extract_tags_from_prompt}


def cmd_tags_build(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    jobs_root = Path(args.jobs_root).expanduser().resolve()
    out_path = Path(args.out or default_asset_tags_path(og_root)).expanduser().resolve()
    doc = build_asset_tags(
        jobs_root=jobs_root,
        provider=str(args.provider),
        out_path=out_path,
        max_tags=int(args.max_tags),
    )
    stats = doc.get("stats") or {}
    print(f"Wrote {out_path}")
    print(f"provider={doc.get('provider')} jobs_scanned={stats.get('jobs_scanned')} tagged_groups={stats.get('tagged_groups')}")
    return 0


def cmd_tags_show(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    path = Path(args.index or default_asset_tags_path(og_root)).expanduser().resolve()
    if not path.is_file():
        print(f"error: asset tags not found: {path}", file=__import__("sys").stderr)
        return 1
    doc = json.loads(path.read_text(encoding="utf-8"))
    tags = lookup_tags(args.key, doc)
    print(json.dumps({"ok": bool(tags), "key": args.key, "tags": tags}, indent=2))
    return 0 if tags else 1


def add_tags_subparser(sub: argparse._SubParsersAction) -> None:
    tags = sub.add_parser("tags", help="Build/query asset tags for tag-coupled discovery")
    tags_sub = tags.add_subparsers(dest="tags_cmd", required=True)

    build = tags_sub.add_parser("build", help="Build asset_tags.json from job prompts (bootstrap)")
    build.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    build.add_argument("--jobs-root", default="/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs")
    build.add_argument("--provider", default="prompt", choices=sorted(PROVIDERS.keys()))
    build.add_argument("--max-tags", type=int, default=24)
    build.add_argument("--out", default=None)
    build.set_defaults(func=cmd_tags_build)

    show = tags_sub.add_parser("show", help="Show tags for a group_id or relpath")
    show.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    show.add_argument("--index", default=None)
    show.add_argument("--key", required=True, help="group_id (og:stem:...) or output relpath")
    show.set_defaults(func=cmd_tags_show)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Asset tagging for tag-coupled discovery")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_tags_subparser(sub)
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
