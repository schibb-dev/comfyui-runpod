#!/usr/bin/env python3
"""
Backfill synthetic jobs for shape-factory outputs that predate job tracking.

"Create the jobs that would have been": for each deposit output in a known shape
family that has no originating job, reconstruct its bindings from the embedded
prompt/workflow (source still/video + prompt), register the assets by content
hash, and write a ``.job.json`` marked ``origin: "backfill"`` / ``status:
"completed"`` so the factory map, lineage, and heuristics treat it uniformly.

Scope: known shape families only (clean graph_hash from the family shape).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import asset_registry as areg  # noqa: E402
import shape_factory_seed_sources as sfss  # noqa: E402

BACKFILL_MARKER = "backfill"
JOB_SCHEMA_VERSION = "comfyui-runpod.shape-job.v0"

_VIDEO_BINDING_TYPES = {"vhs_load_video_path", "vhs_load_video", "load_video"}
_SOURCE_BINDING_TYPES = {"load_image"} | _VIDEO_BINDING_TYPES
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _slug(s: str, maxlen: int = 120) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "")).strip("-")
    return s[:maxlen]


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml
    except ImportError:
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, Exception):  # noqa: BLE001
        return {}


def _shape_requires(shape_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    req = shape_doc.get("requires")
    return [r for r in req if isinstance(r, dict)] if isinstance(req, list) else []


def _text_from_prompt_node(prompt: Dict[str, Any], node_id: Any) -> Optional[str]:
    node = prompt.get(str(node_id)) if isinstance(prompt, dict) else None
    if not isinstance(node, dict):
        return None
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return None
    for key in ("text", "positive", "string", "value", "prompt", "text_g", "text_l"):
        v = inputs.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # Fallback: the longest string input.
    strings = [v for v in inputs.values() if isinstance(v, str) and v.strip()]
    return max(strings, key=len) if strings else None


def _infer_source_video(output_abs: Path, *, ffprobe: Optional[str]) -> Optional[Dict[str, Any]]:
    """First video source path in the output's embedded prompt (VHS loaders)."""
    try:
        from correlate_output_ratings import extract_source_paths_from_prompt, normalize_source_basename
    except ImportError:
        return None
    prompt, evidence = sfss._extract_prompt(output_abs, ffprobe=ffprobe)
    if not isinstance(prompt, dict):
        return None
    for raw in extract_source_paths_from_prompt(prompt):
        bn = normalize_source_basename(raw)
        if bn and Path(bn).suffix.lower() in _VIDEO_EXTS:
            return {"source_raw": raw, "source_basename": bn, "evidence": evidence}
    return None


def _resolve_source_video_abs(
    raw: str, *, workspace_root: Optional[Path], output_root: Path
) -> Path:
    norm = str(raw or "").replace("\\", "/").strip().lstrip("/")
    candidates = []
    if workspace_root is not None:
        candidates.append(workspace_root / norm)
    candidates.append(output_root / norm)
    candidates.append(output_root.parent / norm)
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0] if candidates else Path(norm)


def reconstruct_bindings(
    *,
    output_abs: Path,
    shape_doc: Dict[str, Any],
    workspace_root: Optional[Path],
    output_root: Optional[Path],
    ffprobe: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (bindings, evidence). Bindings mirror live shape-job binding rows."""
    bindings: Dict[str, Any] = {}
    evidence: Dict[str, Any] = {}
    prompt_obj, _label = sfss._extract_prompt(output_abs, ffprobe=ffprobe)

    for req in _shape_requires(shape_doc):
        binding = req.get("binding") or {}
        btype = str(binding.get("type") or "")
        slot = str(req.get("slot") or btype or "source")

        if btype == "load_image":
            still = sfss.infer_source_still(output_abs, ffprobe=ffprobe)
            if not still:
                continue
            rel = sfss.source_still_relpath(still["source_basename"])
            abs_path = (workspace_root / rel) if workspace_root else Path(rel)
            bindings[slot] = {
                "binding_type": "load_image",
                "path": str(abs_path),
                "role": req.get("role"),
                "recovered": True,
            }
            evidence["source_still"] = still.get("evidence")

        elif btype in _VIDEO_BINDING_TYPES:
            vid = _infer_source_video(output_abs, ffprobe=ffprobe)
            if not vid:
                continue
            abs_path = _resolve_source_video_abs(
                vid["source_raw"], workspace_root=workspace_root, output_root=output_root or Path(".")
            )
            bindings[slot] = {
                "binding_type": btype,
                "path": str(abs_path),
                "role": req.get("role"),
                "recovered": True,
                "source_raw": vid.get("source_raw"),
            }
            evidence["source_video"] = vid.get("evidence")

        elif btype == "prompt_bundle":
            row: Dict[str, Any] = {"binding_type": "prompt_bundle", "role": req.get("role"), "recovered": True}
            if isinstance(prompt_obj, dict):
                pos = binding.get("positive") or {}
                neg = binding.get("negative") or {}
                pt = _text_from_prompt_node(prompt_obj, pos.get("node_id")) if isinstance(pos, dict) else None
                nt = _text_from_prompt_node(prompt_obj, neg.get("node_id")) if isinstance(neg, dict) else None
                if pt:
                    row["positive"] = pt
                if nt:
                    row["negative"] = nt
                row["prompt_text_source"] = "embedded" if (pt or nt) else "unavailable"
            bindings[slot] = row
            evidence["prompt_profile"] = row.get("prompt_text_source")

    return bindings, evidence


def _register_assets(
    con: Any,
    *,
    output_abs: Path,
    output_relpath: str,
    bindings: Dict[str, Any],
    workspace_root: Optional[Path],
    job_key: str,
) -> Dict[str, Optional[str]]:
    ids: Dict[str, Optional[str]] = {}
    ids["output"] = areg.register(con, output_abs, relpath=output_relpath, kind="video", refs=[f"job:{job_key}"])
    for slot, b in bindings.items():
        if not isinstance(b, dict):
            continue
        p = str(b.get("path") or "")
        if not p or b.get("binding_type") == "prompt_bundle":
            continue
        abs_p = Path(p)
        if not abs_p.is_file() and workspace_root is not None:
            # path was built workspace-relative; try workspace_root/input/<name>
            cand = workspace_root / "input" / Path(p).name
            if cand.is_file():
                abs_p = cand
        rel = ""
        if workspace_root is not None:
            try:
                rel = str(abs_p.resolve().relative_to(workspace_root.resolve()))
            except ValueError:
                rel = f"input/{abs_p.name}"
        cid = areg.register(con, abs_p, relpath=rel or f"input/{abs_p.name}", refs=[f"job:{job_key}"])
        ids[slot] = cid
    return ids


def synthesize_job(
    *,
    output_abs: Path,
    output_relpath: str,
    family_slug: str,
    shape_doc: Dict[str, Any],
    shape_path: Path,
    pools_path: Optional[Path],
    bindings: Dict[str, Any],
    asset_ids: Dict[str, Optional[str]],
    evidence: Dict[str, Any],
) -> Dict[str, Any]:
    stem = Path(output_relpath).stem
    job_key = _slug(f"{family_slug}__{BACKFILL_MARKER}__{stem}", 160)
    try:
        mtime = datetime.fromtimestamp(output_abs.stat().st_mtime, tz=timezone.utc)
        created_at = mtime.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    except OSError:
        created_at = _utc_now()
    deposits = shape_doc.get("deposits") if isinstance(shape_doc.get("deposits"), dict) else {}
    return {
        "schema_version": JOB_SCHEMA_VERSION,
        "origin": BACKFILL_MARKER,
        "backfill": {
            "created_at": _utc_now(),
            "evidence": evidence,
            "asset_content_ids": asset_ids,
        },
        "created_at": created_at,
        "family_slug": family_slug,
        "shape_id": shape_doc.get("shape_id"),
        "graph_hash": shape_doc.get("graph_hash"),
        "shape_path": str(shape_path),
        "pools_path": str(pools_path) if pools_path else None,
        "job_key": job_key,
        "bindings": bindings,
        "deposits": deposits,
        "submit": {
            "status": "completed",
            "prompt_id": f"backfill-{stem}",
            "prompt_source": "backfill",
            # Absolute path so the map resolves it the same way live jobs /
            # deposit members do (a bare "output/og/..." relpath does not resolve).
            "outputs": [str(output_abs)],
        },
        "outputs": [str(output_relpath)],
        "warnings": [],
    }


def backfill_family(
    *,
    family_slug: str,
    data_root: Path,
    output_root: Path,
    workspace_root: Optional[Path],
    jobs_root: Path,
    registry_con: Any,
    lineage_edges: Optional[List[Dict[str, Any]]] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    from shape_factory_map import resolve_output_relpath  # type: ignore

    ffprobe = __import__("shutil").which("ffprobe")
    family_dir = data_root / "pools" / family_slug
    shape_path = data_root / "shapes" / f"{family_slug}.shape.yaml"
    pools_path = family_dir / "pools.yaml"
    shape_doc = _load_yaml(shape_path)
    if not shape_doc:
        return {"ok": False, "error": "shape_missing", "family": family_slug, "shape_path": str(shape_path)}

    deposits = sfss._collect_seed_deposit_paths(
        data_root, output_root, workspace_root=workspace_root, family_filter=family_slug
    )
    out_dir = jobs_root / family_slug

    created: List[str] = []
    skipped_existing = 0
    no_recon = 0
    for relkey, output_abs in sorted(deposits.items()):
        output_relpath = resolve_output_relpath(str(output_abs), output_root, workspace_root=workspace_root) or relkey
        stem = Path(output_relpath).stem
        job_key = _slug(f"{family_slug}__{BACKFILL_MARKER}__{stem}", 160)
        # Refresh our own backfill jobs, but never clobber a real (submitted) job.
        existing_path = out_dir / f"{job_key}.job.json"
        if existing_path.is_file():
            try:
                prev = json.loads(existing_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prev = {}
            if str(prev.get("origin")) != BACKFILL_MARKER:
                skipped_existing += 1
                continue
        bindings, evidence = reconstruct_bindings(
            output_abs=output_abs,
            shape_doc=shape_doc,
            workspace_root=workspace_root,
            output_root=output_root,
            ffprobe=ffprobe,
        )
        if not any(
            b.get("recovered") and b.get("binding_type") in _SOURCE_BINDING_TYPES
            for b in bindings.values()
        ):
            no_recon += 1
            continue
        asset_ids: Dict[str, Optional[str]] = {}
        if apply and registry_con is not None:
            asset_ids = _register_assets(
                registry_con,
                output_abs=output_abs,
                output_relpath=output_relpath,
                bindings=bindings,
                workspace_root=workspace_root,
                job_key=job_key,
            )
        job = synthesize_job(
            output_abs=output_abs,
            output_relpath=output_relpath,
            family_slug=family_slug,
            shape_doc=shape_doc,
            shape_path=shape_path,
            pools_path=pools_path if pools_path.is_file() else None,
            bindings=bindings,
            asset_ids=asset_ids,
            evidence=evidence,
        )
        if lineage_edges is not None:
            _append_lineage_edge(lineage_edges, output_relpath=output_relpath, bindings=bindings)
        if apply:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{job_key}.job.json").write_text(
                json.dumps(job, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        created.append(job_key)

    return {
        "ok": True,
        "family": family_slug,
        "deposits_scanned": len(deposits),
        "jobs_created": len(created),
        "skipped_existing": skipped_existing,
        "unreconstructable": no_recon,
        "applied": apply,
    }


def _og_group_id(relpath: str) -> str:
    stem = Path(str(relpath or "")).stem.lower()
    return f"og:stem:{stem}" if stem else ""


def _append_lineage_edge(edges: List[Dict[str, Any]], *, output_relpath: str, bindings: Dict[str, Any]) -> None:
    child = _og_group_id(output_relpath)
    if not child:
        return
    for b in bindings.values():
        if not isinstance(b, dict) or not b.get("recovered"):
            continue
        btype = str(b.get("binding_type") or "")
        bn = Path(str(b.get("path") or "")).name
        if not bn:
            continue
        if btype == "load_image":
            parent = f"input:{bn}"
            via = f"input/{bn}"
            ev = "backfill_load_image"
        elif btype in _VIDEO_BINDING_TYPES:
            # Video sources are prior OG outputs -> output->output chain edge.
            parent = _og_group_id(bn)
            via = str(b.get("source_raw") or bn)
            ev = "backfill_load_video"
        else:
            continue
        if not parent or parent == child:
            continue
        edges.append(
            {
                "child_group_id": child,
                "parent_group_id": parent,
                "via_source_raw": via,
                "evidence": ev,
                "updated_at": _utc_now(),
            }
        )


def _merge_lineage_edges(path: Path, new_edges: List[Dict[str, Any]]) -> int:
    doc: Dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                doc = loaded
        except (OSError, json.JSONDecodeError):
            doc = {}
    edges = doc.get("edges")
    if not isinstance(edges, list):
        edges = []
    have = {(str(e.get("child_group_id")), str(e.get("parent_group_id"))) for e in edges if isinstance(e, dict)}
    added = 0
    for e in new_edges:
        key = (str(e.get("child_group_id")), str(e.get("parent_group_id")))
        if key in have:
            continue
        edges.append(e)
        have.add(key)
        added += 1
    doc["edges"] = edges
    doc.setdefault("schema_version", "comfyui-runpod.discovery-lineage.v0")
    doc["updated_at"] = _utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    tmp.replace(path)
    return added


def _known_shape_families(data_root: Path) -> List[str]:
    shapes_dir = data_root / "shapes"
    if not shapes_dir.is_dir():
        return []
    return sorted(p.name[: -len(".shape.yaml")] for p in shapes_dir.glob("*.shape.yaml"))


def cmd_backfill_jobs(args: argparse.Namespace) -> int:
    from shape_factory_map import resolve_shape_factory_data_root  # type: ignore

    output_root = Path(args.output_root).expanduser().resolve()
    workspace_root = Path(args.workspace_root).expanduser().resolve() if args.workspace_root else None
    data_root = (
        Path(args.data_root).expanduser().resolve()
        if args.data_root
        else resolve_shape_factory_data_root(repo_root=Path(args.repo_root).expanduser().resolve())
    )
    jobs_root = Path(args.jobs_root).expanduser().resolve() if args.jobs_root else data_root / "shape_factory" / "jobs"
    og_root = output_root / "og"
    if not og_root.is_dir():
        og_root = output_root / "output" / "og"
    registry_path = Path(args.registry).expanduser().resolve() if args.registry else areg.default_registry_path(og_root)

    if args.all_shaped:
        families = _known_shape_families(data_root)
    elif args.family:
        families = [args.family]
    else:
        print("error: pass --family <slug> or --all-shaped", file=sys.stderr)
        return 2

    con = areg.connect(registry_path) if args.apply else None
    lineage_edges: Optional[List[Dict[str, Any]]] = [] if not args.no_lineage else None
    results = []
    for fam in families:
        results.append(
            backfill_family(
                family_slug=fam,
                data_root=data_root,
                output_root=output_root,
                workspace_root=workspace_root,
                jobs_root=jobs_root,
                registry_con=con,
                lineage_edges=lineage_edges,
                apply=bool(args.apply),
            )
        )
    if con is not None:
        con.close()

    lineage_added = 0
    if args.apply and lineage_edges:
        from shape_factory_heuristics import default_lineage_edges_path  # type: ignore

        lineage_added = _merge_lineage_edges(default_lineage_edges_path(og_root), lineage_edges)

    summary = {
        "ok": all(r.get("ok") for r in results),
        "registry": str(registry_path),
        "jobs_root": str(jobs_root),
        "applied": bool(args.apply),
        "lineage_edges_added": lineage_added,
        "families": results,
    }
    print(json.dumps(summary, indent=2))
    return 0 if summary["ok"] else 1


def add_backfill_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("backfill-jobs", help="Create synthetic completed jobs for job-less shape-family outputs")
    p.add_argument("--family", default=None, help="Family slug (dir under pools/ and shapes/)")
    p.add_argument("--all-shaped", action="store_true", help="All families that have a shape yaml")
    p.add_argument("--output-root", default="/home/yuji/comfyui-runpod-data/output")
    p.add_argument("--workspace-root", default="/home/yuji/comfyui-runpod-data")
    p.add_argument("--data-root", default=None, help="Shape-factory .data root (default: repo .data)")
    p.add_argument("--repo-root", default="/home/yuji/src/comfyui-runpod")
    p.add_argument("--jobs-root", default=None, help="Jobs dir (default: <data_root>/shape_factory/jobs)")
    p.add_argument("--registry", default=None, help="asset_registry.sqlite (default: _status/)")
    p.add_argument("--no-lineage", action="store_true", help="Do not emit input->output lineage edges")
    p.add_argument("--apply", action="store_true", help="Write jobs (default: dry-run summary only)")
    p.set_defaults(func=cmd_backfill_jobs)

    pc = sub.add_parser(
        "backfill-clips",
        help="Import nontrivial *.trims.json presets as Clip bookmarks (idempotent)",
    )
    pc.add_argument("--output-root", default="/home/yuji/comfyui-runpod-data/output")
    pc.add_argument("--registry", default=None, help="asset_registry.sqlite (default: output/_status/)")
    pc.add_argument("--apply", action="store_true", help="Write clips (default: dry-run count only)")
    pc.set_defaults(func=cmd_backfill_clips)

    add_backfill_clips_workflows_subparser(sub)


def cmd_backfill_clips(args: argparse.Namespace) -> int:
    from shape_factory_clips import backfill_clips_from_trims_sidecars

    summary = backfill_clips_from_trims_sidecars(
        output_root=Path(args.output_root).expanduser(),
        registry_path=Path(args.registry).expanduser() if args.registry else None,
        apply=bool(args.apply),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("ok") else 1


def cmd_backfill_clips_from_workflows(args: argparse.Namespace) -> int:
    from shape_factory_clips import backfill_clips_from_workflows

    summary = backfill_clips_from_workflows(
        workflows_root=Path(args.workflows_root).expanduser(),
        output_root=Path(args.output_root).expanduser(),
        data_root=Path(args.data_root).expanduser() if args.data_root else None,
        jobs_root=Path(args.jobs_root).expanduser() if args.jobs_root else None,
        registry_path=Path(args.registry).expanduser() if args.registry else None,
        apply=bool(args.apply),
        top=int(args.top),
        include_template_skips=bool(args.include_template_skips),
        set_default=not bool(args.no_default),
    )
    # Keep dry-run readable: full candidate list; apply: drop bulky samples if huge
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("ok") else 1


def add_backfill_clips_workflows_subparser(sub: argparse._SubParsersAction) -> None:
    pw = sub.add_parser(
        "backfill-clips-from-workflows",
        help="Farm Clip bookmarks from VHS windows embedded in saved UI workflows",
    )
    pw.add_argument(
        "--workflows-root",
        default="/home/yuji/comfyui-runpod-data/comfyui_user/default/workflows",
    )
    pw.add_argument("--output-root", default="/home/yuji/comfyui-runpod-data/output")
    pw.add_argument(
        "--data-root",
        default="/home/yuji/comfyui-runpod-data",
        help="Comfy data root for resolving output/… paths",
    )
    pw.add_argument(
        "--jobs-root",
        default="/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs",
        help="Factory jobs dir (downstream source_video gravity)",
    )
    pw.add_argument("--registry", default=None, help="asset_registry.sqlite (default: output/_status/)")
    pw.add_argument("--top", type=int, default=100, help="Max ranked editorial windows to import")
    pw.add_argument(
        "--include-template-skips",
        action="store_true",
        help="Also import bare template skips (47/57/85 with cap=0)",
    )
    pw.add_argument("--no-default", action="store_true", help="Do not set default_clip on parents")
    pw.add_argument("--apply", action="store_true", help="Write clips (default: dry-run candidates)")
    pw.set_defaults(func=cmd_backfill_clips_from_workflows)

    pp = sub.add_parser(
        "backfill-clips-from-pngs",
        help="Farm Clip bookmarks from VHS windows in companion PNG embeds (asset-centric)",
    )
    pp.add_argument("--output-root", default="/home/yuji/comfyui-runpod-data/output")
    pp.add_argument("--data-root", default="/home/yuji/comfyui-runpod-data")
    pp.add_argument(
        "--jobs-root",
        default="/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs",
    )
    pp.add_argument("--registry", default=None)
    pp.add_argument("--top", type=int, default=150, help="Max ranked editorial windows to import")
    pp.add_argument("--max-pngs", type=int, default=0, help="Limit PNG scan (0=all under output/og)")
    pp.add_argument("--include-template-skips", action="store_true")
    pp.add_argument("--no-default", action="store_true")
    pp.add_argument("--apply", action="store_true")
    pp.set_defaults(func=cmd_backfill_clips_from_pngs)


def cmd_backfill_clips_from_pngs(args: argparse.Namespace) -> int:
    from shape_factory_clips import backfill_clips_from_companion_pngs

    summary = backfill_clips_from_companion_pngs(
        output_root=Path(args.output_root).expanduser(),
        data_root=Path(args.data_root).expanduser() if args.data_root else None,
        jobs_root=Path(args.jobs_root).expanduser() if args.jobs_root else None,
        registry_path=Path(args.registry).expanduser() if args.registry else None,
        apply=bool(args.apply),
        top=int(args.top),
        include_template_skips=bool(args.include_template_skips),
        set_default=not bool(args.no_default),
        max_pngs=int(args.max_pngs or 0),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Shape-factory job backfill")
    _sub = ap.add_subparsers(dest="cmd", required=True)
    add_backfill_subparser(_sub)
    _args = ap.parse_args()
    raise SystemExit(_args.func(_args))
