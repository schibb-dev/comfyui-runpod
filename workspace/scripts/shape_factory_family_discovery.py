#!/usr/bin/env python3
"""Cluster workflow corpus for Phase 2 family discovery.

Scans catalog readable JSONs, template candidates, and user workflow trees;
groups by structural graph fingerprint; marks clusters covered by enrolled shapes.

Usage:
  python3 shape_factory_family_discovery.py cluster [--write docs/family_discovery]
  python3 shape_factory_family_discovery.py enroll --prop prop_003 --slug MyFamily ...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from shape_factory import load_yaml  # noqa: E402
from shape_factory_vocab import (  # noqa: E402
    format_catalog_stem,
    graph_fingerprint_lite,
    guess_io_from_workflow,
    load_workflow_json,
    parse_catalog_stem,
    validate_shape_document,
)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_DATA = REPO / ".data"
DEFAULT_CATALOG = Path(
    "/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows/generated/catalog"
)
DEFAULT_USER_WF = Path("/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows")
DEFAULT_CANDIDATES = DEFAULT_DATA / "template_candidates"
DEFAULT_OUT = REPO / "docs" / "family_discovery"

NOISE_NAME_RE = re.compile(r"(?i)(^|[_-])(tune-|tunetest|TUNETEST)")


def _utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_noise(path: Path) -> bool:
    name = path.name
    if NOISE_NAME_RE.search(name):
        return True
    if name.endswith(".bak") or ".bak." in name or name.endswith(".bak2"):
        return True
    if "bak-before" in name or name.endswith("~"):
        return True
    return False


def _enrolled_fingerprints(shapes_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Map fingerprint → enrolled family meta (from shape template when present)."""
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(shapes_dir.glob("*.shape.yaml")):
        doc = load_yaml(path)
        slug = str(doc.get("family_slug") or path.name[: -len(".shape.yaml")])
        meta = {
            "family_slug": slug,
            "shape_path": str(path),
            "io_class": doc.get("io_class"),
            "chain_role": doc.get("chain_role"),
            "graph_hash": doc.get("graph_hash"),
        }
        gh = str(doc.get("graph_hash") or "").strip()
        if gh:
            out[gh] = meta
        tpl = doc.get("template")
        if tpl:
            wf = load_workflow_json(Path(str(tpl)))
            if wf:
                fp = graph_fingerprint_lite(wf)
                out[fp] = meta
                meta["fingerprint"] = fp
    return out


def _iter_workflow_paths(
    *,
    catalog_dir: Path,
    user_dir: Path,
    candidates_dir: Path,
) -> Iterable[Tuple[str, Path]]:
    if catalog_dir.is_dir():
        for p in sorted(catalog_dir.glob("*-readable.json")):
            if _is_noise(p):
                continue
            yield "catalog", p
    if candidates_dir.is_dir():
        for p in sorted(candidates_dir.glob("*.candidate.json")):
            if _is_noise(p):
                continue
            yield "candidate", p
    if user_dir.is_dir():
        for p in sorted(user_dir.rglob("*.json")):
            if _is_noise(p):
                continue
            # skip generated/catalog (already scanned as catalog)
            try:
                rel = p.relative_to(user_dir)
            except ValueError:
                continue
            parts = rel.parts
            if parts and parts[0] == "generated":
                continue
            if any(part.startswith(".") for part in parts):
                continue
            yield "user", p


def _load_candidate_workflow(path: Path) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    for key in ("workflow", "ui_workflow", "template", "graph"):
        inner = obj.get(key)
        if isinstance(inner, dict) and (inner.get("nodes") or inner.get("links") is not None):
            return inner
    if obj.get("nodes"):
        return obj
    return None


def cluster_corpus(
    *,
    shapes_dir: Path,
    catalog_dir: Path,
    user_dir: Path,
    candidates_dir: Path,
) -> Dict[str, Any]:
    enrolled = _enrolled_fingerprints(shapes_dir)
    clusters: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []
    seen_paths: Set[str] = set()

    for source, path in _iter_workflow_paths(
        catalog_dir=catalog_dir, user_dir=user_dir, candidates_dir=candidates_dir
    ):
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        if source == "candidate":
            wf = _load_candidate_workflow(path)
        else:
            wf = load_workflow_json(path)
        if not wf or not isinstance(wf.get("nodes"), list):
            errors.append({"path": str(path), "source": source, "error": "unreadable_or_not_litegraph"})
            continue
        fp = graph_fingerprint_lite(wf)
        stem_info = parse_catalog_stem(path.name)
        guess = guess_io_from_workflow(wf)
        bucket = clusters.setdefault(
            fp,
            {
                "fingerprint": fp,
                "members": [],
                "covered_by": None,
                "io_guess": guess,
            },
        )
        bucket["members"].append(
            {
                "source": source,
                "path": str(path),
                "name": path.name,
                "stem": stem_info if stem_info.get("ok") else None,
                "node_count": len(wf.get("nodes") or []),
            }
        )
        if fp in enrolled and not bucket["covered_by"]:
            bucket["covered_by"] = enrolled[fp]

    # Also mark covered when shape graph_hash matches even if template path differs
    for fp, bucket in clusters.items():
        if bucket["covered_by"]:
            continue
        # nearest: if any member name matches enrolled family slug as prefix
        for slug_meta in enrolled.values():
            slug = slug_meta["family_slug"]
            for m in bucket["members"]:
                if str(m["name"]).startswith(slug) or slug in str(m["name"]):
                    # weak name match — only if fingerprint equals enrolled hash
                    if fp == slug_meta.get("graph_hash") or fp == slug_meta.get("fingerprint"):
                        bucket["covered_by"] = slug_meta
                        break

    rows = sorted(
        clusters.values(),
        key=lambda b: (-len(b["members"]), b["fingerprint"][:12]),
    )
    uncovered = [b for b in rows if not b.get("covered_by")]
    covered = [b for b in rows if b.get("covered_by")]

    return {
        "schema_version": "comfyui-runpod.family-discovery.v0",
        "generated_at": _utc(),
        "counts": {
            "clusters": len(rows),
            "covered_clusters": len(covered),
            "uncovered_clusters": len(uncovered),
            "workflow_files": sum(len(b["members"]) for b in rows),
            "errors": len(errors),
        },
        "enrolled_families": sorted({m["family_slug"] for m in enrolled.values()}),
        "clusters": rows,
        "errors": errors[:50],
    }


def _sample_videos_for_name(name: str, output_root: Path, limit: int = 3) -> List[str]:
    """Best-effort: find a few og videos whose basename shares a brand token."""
    brand = re.split(r"[_\-]", Path(name).stem.replace("-readable", ""))[0]
    if len(brand) < 3:
        return []
    hits: List[str] = []
    og = output_root / "og"
    if not og.is_dir():
        return []
    # shallow scan recent date dirs
    dates = sorted([p for p in og.iterdir() if p.is_dir()], reverse=True)[:14]
    for d in dates:
        for mp4 in d.rglob("*.mp4"):
            if brand.lower() in mp4.name.lower():
                hits.append(str(mp4))
                if len(hits) >= limit:
                    return hits
    return hits


def write_proposal_cards(
    report: Dict[str, Any],
    out_dir: Path,
    *,
    output_root: Path,
    max_props: int = 40,
) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    uncovered = [c for c in report["clusters"] if not c.get("covered_by")]
    # Prefer clusters with catalog/candidate evidence over pure user one-offs
    def rank(c: Dict[str, Any]) -> Tuple[int, int, str]:
        sources = {m["source"] for m in c["members"]}
        weight = (2 if "catalog" in sources else 0) + (1 if "candidate" in sources else 0)
        return (-weight, -len(c["members"]), c["fingerprint"])

    uncovered.sort(key=rank)
    written: List[Path] = []
    index_rows: List[Dict[str, Any]] = []

    for i, cluster in enumerate(uncovered[:max_props], start=1):
        prop_id = f"prop_{i:03d}"
        guess = cluster.get("io_guess") or {}
        members = cluster["members"]
        rep = members[0]
        videos = _sample_videos_for_name(rep["name"], output_root)
        card = {
            "id": prop_id,
            "status": "pending_review",  # new_family | merge | skip
            "proposed_family_slug": None,
            "fingerprint": cluster["fingerprint"],
            "io_guess": guess.get("io_class"),
            "primary_input_guess": guess.get("primary_input"),
            "input_profile_guess": guess.get("input_profile"),
            "chain_role_guess": guess.get("chain_role_guess"),
            "member_count": len(members),
            "representative": rep,
            "members": members[:12],
            "sample_videos": videos,
            "quarantine_notes": [],
            "nearest_enrolled": None,
            "operator_decision": None,
            "operator_notes": None,
        }
        path = out_dir / f"{prop_id}.json"
        path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        # Human-readable sidecar
        md = out_dir / f"{prop_id}.md"
        lines = [
            f"# {prop_id}",
            "",
            f"- **status:** `{card['status']}`",
            f"- **IO guess:** `{card['io_guess']}` · profile `{card['input_profile_guess']}` · role `{card['chain_role_guess']}`",
            f"- **fingerprint:** `{card['fingerprint'][:16]}…`",
            f"- **members:** {card['member_count']}",
            f"- **representative:** `{rep['path']}`",
            "",
            "## Sample videos",
            "",
        ]
        if videos:
            lines.extend(f"- `{v}`" for v in videos)
        else:
            lines.append("_none found by brand heuristic — locate manually_")
        lines.extend(["", "## Members", ""])
        for m in members[:12]:
            lines.append(f"- [{m['source']}] `{m['path']}`")
        lines.extend(
            [
                "",
                "## Operator gate",
                "",
                "- [ ] new family — set `proposed_family_slug`",
                "- [ ] merge into existing — note target slug",
                "- [ ] skip",
                "",
            ]
        )
        md.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.extend([path, md])
        index_rows.append(
            {
                "id": prop_id,
                "io_guess": card["io_guess"],
                "members": card["member_count"],
                "representative": rep["name"],
                "status": card["status"],
            }
        )

    summary = {
        "schema_version": "comfyui-runpod.family-discovery-index.v0",
        "generated_at": _utc(),
        "cluster_report": "cluster_report.json",
        "proposals": index_rows,
        "covered_clusters": report["counts"]["covered_clusters"],
        "uncovered_clusters": report["counts"]["uncovered_clusters"],
        "review_instructions": (
            "For each prop_NNN: watch sample videos, then set status in the JSON to "
            "new_family|merge|skip and fill proposed_family_slug or merge target. "
            "Then run: python3 shape_factory_family_discovery.py enroll --prop prop_NNN ..."
        ),
    }
    (out_dir / "INDEX.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "cluster_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "REVIEW.md").write_text(
        "\n".join(
            [
                "# Family discovery — operator review",
                "",
                f"Generated `{summary['generated_at']}`.",
                "",
                f"- Covered clusters (already enrolled): **{summary['covered_clusters']}**",
                f"- Uncovered clusters with proposals: **{len(index_rows)}** "
                f"(of {summary['uncovered_clusters']} uncovered)",
                "",
                "## How to review",
                "",
                "1. Open each `prop_NNN.md` and watch listed sample videos (or locate better ones).",
                "2. Decide: **new family** / **merge** into an enrolled slug / **skip**.",
                "3. Edit the matching `prop_NNN.json`: set `status`, `proposed_family_slug` "
                "(or `nearest_enrolled` for merge), and `operator_notes`.",
                "4. For approved new families, run enroll (scaffolds shape/pools with Phase 1 fields).",
                "",
                "## Proposal index",
                "",
                "| id | IO | members | representative | status |",
                "|----|----|---------|----------------|--------|",
            ]
            + [
                f"| {r['id']} | {r['io_guess'] or '—'} | {r['members']} | `{r['representative']}` | {r['status']} |"
                for r in index_rows
            ]
            + ["", "No families are auto-enrolled. Naming is the human gate.", ""]
        ),
        encoding="utf-8",
    )
    written.append(out_dir / "REVIEW.md")
    return written


def enroll_from_prop(
    *,
    prop_path: Path,
    slug: str,
    shapes_dir: Path,
    pools_dir: Path,
    catalog_dir: Path,
    io_class: Optional[str] = None,
    chain_role: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Scaffold shape+pools for an approved proposal (does not auto-fix templates)."""
    card = json.loads(prop_path.read_text(encoding="utf-8"))
    if card.get("status") not in {"new_family", "approved"}:
        raise RuntimeError(f"{prop_path.name} status must be new_family/approved (got {card.get('status')!r})")
    rep = card.get("representative") or {}
    src = Path(str(rep.get("path") or ""))
    if not src.is_file():
        raise RuntimeError(f"representative missing: {src}")
    io = io_class or card.get("io_guess") or "I2V"
    role = chain_role or card.get("chain_role_guess") or "standalone"
    profile = {
        "I2V": "still_prompt",
        "V2V": "video_prompt",
        "VI2V": "video_identity_still_prompt",
    }.get(str(io).upper(), "still_prompt")
    primary = "still" if profile == "still_prompt" else "video"
    shape_id = {
        "still_prompt": "wan-i2v-still+prompt",
        "video_prompt": "wan-v2v-source+prompt",
        "video_identity_still_prompt": "wan-vi2v-source+identity_still+prompt",
    }[profile]

    now = datetime.now(tz=timezone.utc)
    stem = format_catalog_stem(
        slug,
        date=now.strftime("%Y-%m-%d"),
        time=now.strftime("%H%M%S"),
        io_class=io,
        seq=1,
    )
    catalog_name = f"{stem}-readable.json"
    catalog_path = catalog_dir / catalog_name
    shape_path = shapes_dir / f"{slug}.shape.yaml"
    family_pools = pools_dir / slug
    pools_path = family_pools / "pools.yaml"

    wf = load_workflow_json(src) if src.suffix == ".json" and "candidate" not in src.name else None
    if wf is None and "candidate" in src.name:
        wf = _load_candidate_workflow(src)
    if wf is None:
        wf = load_workflow_json(src)
    if not wf:
        raise RuntimeError(f"cannot load workflow from {src}")

    fp = graph_fingerprint_lite(wf)
    # Minimal requires — operator must wire node_ids before generate
    if profile == "still_prompt":
        requires = [
            {
                "slot": "source_still",
                "role": "A",
                "media": "image",
                "binding": {"type": "load_image", "node_id": 88, "field": "image"},
            },
            {
                "slot": "prompt_profile",
                "role": "C",
                "media": "text",
                "binding": {
                    "type": "prompt_bundle",
                    "positive": {"node_id": 408, "widget_index": 0},
                    "negative": {"node_id": 409, "widget_index": 0},
                },
            },
        ]
    elif profile == "video_identity_still_prompt":
        requires = [
            {
                "slot": "source_video",
                "role": "B",
                "media": "video",
                "binding": {"type": "vhs_load_video_path", "node_id": 377, "field": "video"},
            },
            {
                "slot": "identity_anchor",
                "role": "A",
                "media": "image",
                "binding": {"type": "load_image", "node_id": 500, "field": "image"},
            },
            {
                "slot": "prompt_profile",
                "role": "C",
                "media": "text",
                "binding": {
                    "type": "prompt_bundle",
                    "positive": {"node_id": 380, "widget_index": 0},
                    "negative": {"node_id": 17, "widget_index": 0},
                },
            },
        ]
    else:
        requires = [
            {
                "slot": "source_video",
                "role": "B",
                "media": "video",
                "binding": {"type": "vhs_load_video_path", "node_id": 377, "field": "video"},
            },
            {
                "slot": "prompt_profile",
                "role": "C",
                "media": "text",
                "binding": {
                    "type": "prompt_bundle",
                    "positive": {"node_id": 380, "widget_index": 0},
                    "negative": {"node_id": 17, "widget_index": 0},
                },
            },
        ]

    shape_doc = {
        "schema_version": "comfyui-runpod.shape.v0",
        "shape_id": shape_id,
        "family_slug": slug,
        "primary_input": primary,
        "input_profile": profile,
        "chain_role": role,
        "io_class": str(io).upper(),
        "graph_hash": fp,
        "template": str(catalog_path),
        "requires": requires,
        "produces": [
            {
                "slot": "final_video",
                "role": "X",
                "media": "video",
                "binding": {"node_id": 80 if profile != "still_prompt" else 398, "node_type": "VHS_VideoCombine"},
            }
        ],
        "deposits": {"final_video": {"to_pool": f"pool:{slug}_X_og"}},
        "output_prefix_root": f"og/%date:yyyy-MM-dd%/{slug}_shape",
        "rules": [],
    }

    result = {
        "slug": slug,
        "catalog_path": str(catalog_path),
        "shape_path": str(shape_path),
        "pools_path": str(pools_path),
        "stem": stem,
        "dry_run": dry_run,
        "validate_errors": validate_shape_document(shape_doc, check_start_image=False),
    }
    if dry_run:
        return result

    catalog_dir.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Re-validate with template on disk
    result["validate_errors"] = validate_shape_document(shape_doc, check_start_image=True)

    import yaml

    shape_path.write_text(yaml.safe_dump(shape_doc, sort_keys=False), encoding="utf-8")
    family_pools.mkdir(parents=True, exist_ok=True)
    (family_pools / "prompts").mkdir(exist_ok=True)
    pools_doc = {
        "schema_version": "comfyui-runpod.pools.v0",
        "shape": str(shape_path.resolve()),
        "pools": {},
        "deposit_pools": {
            f"{slug}_X_og": {
                "slot": "final_video",
                "description": f"{slug} final_video",
                "seed_members": [],
            }
        },
    }
    for req in requires:
        slot = req["slot"]
        pools_doc["pools"][slot] = {"slot": slot, "members": []}
    pools_path.write_text(yaml.safe_dump(pools_doc, sort_keys=False), encoding="utf-8")

    card["status"] = "enrolled"
    card["proposed_family_slug"] = slug
    card["enrolled_at"] = _utc()
    card["enrolled_shape"] = str(shape_path)
    prop_path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("cluster", help="Cluster corpus and write proposal cards")
    c.add_argument("--shapes-dir", type=Path, default=DEFAULT_DATA / "shapes")
    c.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    c.add_argument("--user-dir", type=Path, default=DEFAULT_USER_WF)
    c.add_argument("--candidates-dir", type=Path, default=DEFAULT_CANDIDATES)
    c.add_argument("--output-root", type=Path, default=Path("/home/yuji/comfyui-runpod-data/output"))
    c.add_argument("--write", type=Path, default=DEFAULT_OUT)
    c.add_argument("--max-props", type=int, default=40)

    e = sub.add_parser("enroll", help="Scaffold shape+pools from an approved prop card")
    e.add_argument("--prop", required=True, help="prop id (prop_001) or path to prop JSON")
    e.add_argument("--slug", required=True, help="family_slug to enroll")
    e.add_argument("--discovery-dir", type=Path, default=DEFAULT_OUT)
    e.add_argument("--shapes-dir", type=Path, default=DEFAULT_DATA / "shapes")
    e.add_argument("--pools-dir", type=Path, default=DEFAULT_DATA / "pools")
    e.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    e.add_argument("--io-class", default=None)
    e.add_argument("--chain-role", default=None)
    e.add_argument("--dry-run", action="store_true")

    v = sub.add_parser("validate-shapes", help="Validate enrolled shape vocabulary + start_image")
    v.add_argument("--shapes-dir", type=Path, default=DEFAULT_DATA / "shapes")

    args = ap.parse_args(argv)
    if args.cmd == "cluster":
        report = cluster_corpus(
            shapes_dir=args.shapes_dir,
            catalog_dir=args.catalog_dir,
            user_dir=args.user_dir,
            candidates_dir=args.candidates_dir,
        )
        write_proposal_cards(
            report,
            args.write,
            output_root=args.output_root,
            max_props=args.max_props,
        )
        print(json.dumps(report["counts"], indent=2))
        print(f"wrote proposals under {args.write}")
        return 0

    if args.cmd == "enroll":
        prop = Path(args.prop)
        if not prop.is_file():
            prop = args.discovery_dir / f"{args.prop}.json"
        result = enroll_from_prop(
            prop_path=prop,
            slug=args.slug,
            shapes_dir=args.shapes_dir,
            pools_dir=args.pools_dir,
            catalog_dir=args.catalog_dir,
            io_class=args.io_class,
            chain_role=args.chain_role,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "validate-shapes":
        bad = 0
        for path in sorted(args.shapes_dir.glob("*.shape.yaml")):
            doc = load_yaml(path)
            errs = validate_shape_document(doc, check_start_image=True)
            if errs:
                bad += 1
                print(f"FAIL {path.name}")
                for e in errs:
                    print(f"  - {e}")
            else:
                print(f"OK   {path.name}")
        return 1 if bad else 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
