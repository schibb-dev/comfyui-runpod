"""Review and gated purge of appetite=remove assets.

Straightforward delete is allowed only when nothing else depends on the asset
as a source (pool membership / downstream jobs). After the file is gone, the
producing job is archived (``.job.json.discarded``) when it has no remaining
videos. Ratings and appetite rows are always removed with the file.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from shape_factory_ratings import delete_judgment_for_relpath, load_appetite_doc, normalize_appetite, path_appetite_state


def _posix(raw: Any) -> str:
    return str(raw or "").replace("\\", "/").strip()


def _path_keys(raw: Any) -> Set[str]:
    text = _posix(raw)
    if not text:
        return set()
    keys: Set[str] = {text, Path(text).name}
    low = text.lower()
    if "/og/" in low:
        keys.add("og/" + text.split("/og/", 1)[-1].lstrip("/"))
    if "/input/" in low:
        keys.add("input/" + text.split("/input/", 1)[-1].lstrip("/"))
    elif low.startswith("input/"):
        keys.add(text)
    if "/output/" in low:
        keys.add(text.split("/output/", 1)[-1].lstrip("/"))
    return {k.lower() for k in keys if k}


def _canonical_relpath(raw: Any) -> str:
    text = _posix(raw)
    if not text:
        return ""
    low = text.lower()
    if "/og/" in low:
        return "og/" + text.split("/og/", 1)[-1].lstrip("/")
    if low.startswith("og/"):
        return text.lstrip("/")
    if "/input/" in low:
        return "input/" + text.split("/input/", 1)[-1].lstrip("/")
    if low.startswith("output/"):
        return text[len("output/") :].lstrip("/")
    return text


def list_remove_relpaths(appetite_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    table = appetite_doc.get("by_output_relpath") if isinstance(appetite_doc, dict) else None
    if not isinstance(table, dict):
        return []
    seen: Set[str] = set()
    rows: List[Dict[str, Any]] = []
    for key, row in table.items():
        if not isinstance(row, dict):
            continue
        if normalize_appetite(row.get("appetite")) != "remove":
            continue
        rel = _canonical_relpath(key) or _posix(key)
        ident = rel.lower()
        if not rel or ident in seen:
            continue
        seen.add(ident)
        rows.append(
            {
                "relpath": rel,
                "facet": str(row.get("facet") or row.get("appetite_facet") or "both"),
                "updated_at": row.get("updated_at"),
            }
        )
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows


def _index_jobs(jobs_dir: Path) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    """Map path keys → jobs that use the asset as output vs source."""
    as_output: Dict[str, List[Dict[str, Any]]] = {}
    as_source: Dict[str, List[Dict[str, Any]]] = {}
    if not jobs_dir.is_dir():
        return as_output, as_source

    def add(bucket: Dict[str, List[Dict[str, Any]]], keys: Set[str], rec: Dict[str, Any]) -> None:
        for key in keys:
            bucket.setdefault(key, []).append(rec)

    for path in jobs_dir.rglob("*.job.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(job, dict):
            continue
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        rec = {
            "job_key": str(job.get("job_key") or path.stem),
            "family": str(job.get("family_slug") or job.get("family") or path.parent.name),
            "status": str(submit.get("status") or job.get("status") or ""),
            "job_path": str(path),
        }
        out_keys: Set[str] = set()
        out_keys |= _path_keys(job.get("output_prefix"))
        outputs = submit.get("outputs") if isinstance(submit.get("outputs"), list) else []
        for item in outputs:
            out_keys |= _path_keys(item)
        add(as_output, out_keys, rec)

        src_keys: Set[str] = set()
        bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
        for meta in bindings.values():
            if isinstance(meta, dict):
                src_keys |= _path_keys(meta.get("path"))
            elif isinstance(meta, str):
                src_keys |= _path_keys(meta)
        add(as_source, src_keys, rec)
    return as_output, as_source


def _index_pools(pools_root: Path) -> Dict[str, List[Dict[str, Any]]]:
    hits: Dict[str, List[Dict[str, Any]]] = {}
    if not pools_root.is_dir():
        return hits
    for index_path in pools_root.glob("*/index.json"):
        try:
            doc = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        family = index_path.parent.name
        pools = doc.get("pools") if isinstance(doc, dict) else None
        if not isinstance(pools, dict):
            continue
        for pool_name, pool in pools.items():
            if not isinstance(pool, dict):
                continue
            members = pool.get("members") if isinstance(pool.get("members"), list) else []
            for member in members:
                raw = member.get("path") if isinstance(member, dict) else member
                rec = {"family": family, "pool": str(pool_name), "path": _posix(raw)}
                for key in _path_keys(raw):
                    hits.setdefault(key, []).append(rec)
    return hits


def _dedupe_recs(
    rows: Iterable[Dict[str, Any]],
    keyfn: Callable[[Dict[str, Any]], str],
) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for row in rows:
        ident = keyfn(row)
        if not ident or ident in seen:
            continue
        seen.add(ident)
        out.append(row)
    return out


def analyze_remove_item(
    relpath: str,
    *,
    as_output: Dict[str, List[Dict[str, Any]]],
    as_source: Dict[str, List[Dict[str, Any]]],
    pools: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    keys = _path_keys(relpath)
    output_jobs = _dedupe_recs((hit for k in keys for hit in as_output.get(k, [])), lambda r: str(r.get("job_key") or ""))
    source_jobs = _dedupe_recs((hit for k in keys for hit in as_source.get(k, [])), lambda r: str(r.get("job_key") or ""))
    pool_hits = _dedupe_recs(
        (hit for k in keys for hit in pools.get(k, [])),
        lambda r: f"{r.get('family')}|{r.get('pool')}|{r.get('path')}",
    )
    inflight = [
        j
        for j in source_jobs
        if str(j.get("status") or "").strip().lower() in {"pending", "queued", "submitted", "running"}
    ]
    blockers: List[str] = []
    if inflight:
        blockers.append(f"{len(inflight)} in-flight job(s) still bind this as a source")
    if source_jobs:
        blockers.append(f"{len(source_jobs)} job(s) reference this as a source")
    if pool_hits:
        blockers.append(f"listed in {len(pool_hits)} pool index membership(s)")
    notes: List[str] = []
    if output_jobs:
        notes.append(f"{len(output_jobs)} job record(s) name this as an output (expected lineage)")
    return {
        "relpath": relpath,
        "as_output_jobs": output_jobs[:12],
        "as_source_jobs": source_jobs[:12],
        "pool_memberships": pool_hits[:12],
        "inflight_source_jobs": inflight[:12],
        "counts": {
            "as_output_jobs": len(output_jobs),
            "as_source_jobs": len(source_jobs),
            "pool_memberships": len(pool_hits),
            "inflight_source_jobs": len(inflight),
        },
        "purge_ready": not inflight and not source_jobs and not pool_hits,
        "blockers": blockers,
        "notes": notes,
        "deletion": "ready" if (not inflight and not source_jobs and not pool_hits) else "blocked",
    }


def build_remove_review(
    *,
    appetite_index_path: Path,
    jobs_dir: Path,
    pools_root: Path,
    limit: int = 200,
) -> Dict[str, Any]:
    appetite_doc = load_appetite_doc(appetite_index_path)
    marked = list_remove_relpaths(appetite_doc)
    if not marked:
        return {
            "ok": True,
            "count": 0,
            "shown": 0,
            "purge_ready": 0,
            "has_references": 0,
            "deletion": "ready_only",
            "items": [],
        }
    as_output, as_source = _index_jobs(jobs_dir)
    pools = _index_pools(pools_root)
    items = []
    try:
        shown_limit = max(0, int(limit))
    except (TypeError, ValueError):
        shown_limit = 200
    for row in marked[:shown_limit]:
        analysis = analyze_remove_item(
            str(row["relpath"]),
            as_output=as_output,
            as_source=as_source,
            pools=pools,
        )
        analysis["facet"] = row.get("facet")
        analysis["updated_at"] = row.get("updated_at")
        analysis["appetite"] = "remove"
        items.append(analysis)
    return {
        "ok": True,
        "count": len(marked),
        "shown": len(items),
        "purge_ready": sum(1 for it in items if it.get("purge_ready")),
        "has_references": sum(1 for it in items if not it.get("purge_ready")),
        "deletion": "ready_only",
        "items": items,
    }


_MEDIA_SUFFIXES = (".mp4", ".webm", ".mov", ".png", ".jpeg", ".jpg", ".webp")
_SIDECAR_SUFFIXES = (
    ".XMP",
    ".xmp",
    ".png",
    ".webp",
    ".jpg",
    ".jpeg",
    ".json",
    ".metadata.json",
    ".trims.json",
)


def _safe_under(root: Path, rel: str) -> Optional[Path]:
    text = _posix(rel)
    if not text or ".." in Path(text).parts:
        return None
    try:
        base = Path(root).expanduser().resolve()
        cand = (base / text).resolve()
        cand.relative_to(base)
    except (OSError, ValueError):
        return None
    return cand


def resolve_media_files(relpath: str, search_roots: Sequence[Path]) -> List[Path]:
    text = _posix(relpath)
    if not text:
        return []
    variants = [text]
    can = _canonical_relpath(text)
    if can and can not in variants:
        variants.append(can)
    if can.startswith("og/") and f"output/{can}" not in variants:
        variants.append(f"output/{can}")
    expanded: List[str] = []
    seen_v: Set[str] = set()
    for variant in variants:
        if variant not in seen_v:
            seen_v.add(variant)
            expanded.append(variant)
        if not Path(variant).suffix:
            for suffix in _MEDIA_SUFFIXES:
                extra = variant + suffix
                if extra not in seen_v:
                    seen_v.add(extra)
                    expanded.append(extra)
    variants = expanded
    found: List[Path] = []
    seen: Set[str] = set()
    for root in search_roots:
        if not root:
            continue
        for variant in variants:
            cand = _safe_under(root, variant)
            if cand is None or not cand.is_file():
                continue
            ident = str(cand)
            if ident in seen:
                continue
            seen.add(ident)
            found.append(cand)
    return found


def sidecar_files_for_media(media: Path) -> List[Path]:
    out: List[Path] = []
    stem = media.stem
    parent = media.parent
    if not parent.is_dir():
        return out
    for suffix in _SIDECAR_SUFFIXES:
        cand = parent / f"{stem}{suffix}"
        if cand.is_file() and cand.resolve() != media.resolve():
            out.append(cand)
    return out


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}


def _expanded_path_keys(raw: Any) -> Set[str]:
    keys = set(_path_keys(raw))
    extra: Set[str] = set(keys)
    for key in keys:
        extra.add(Path(key).stem.lower())
        extra.add(Path(key).name.lower())
        for suffix in _MEDIA_SUFFIXES:
            if key.endswith(suffix):
                extra.add(key[: -len(suffix)])
                extra.add(Path(key[: -len(suffix)]).name.lower())
    return {k for k in extra if k}


def _listed_job_outputs(job: Dict[str, Any]) -> List[str]:
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    deposit = job.get("deposit") if isinstance(job.get("deposit"), dict) else {}
    out: List[str] = []
    for src in (submit.get("outputs"), deposit.get("videos")):
        if not isinstance(src, list):
            continue
        for item in src:
            text = _posix(item)
            if text and text not in out:
                out.append(text)
    return out


def _output_still_on_disk(raw: str, search_roots: Sequence[Path]) -> bool:
    text = _posix(raw)
    if not text:
        return False
    p = Path(text)
    try:
        if p.is_file():
            return True
    except OSError:
        pass
    return bool(resolve_media_files(text, search_roots))


def _remaining_job_videos(
    job: Dict[str, Any],
    purged_rel: str,
    search_roots: Sequence[Path],
) -> List[str]:
    purged_keys = _expanded_path_keys(purged_rel)
    remaining: List[str] = []
    seen: Set[str] = set()

    def _keep(raw: str) -> None:
        text = _posix(raw)
        if not text:
            return
        ident = text.lower()
        if ident in seen:
            return
        if _expanded_path_keys(text) & purged_keys:
            return
        if not _output_still_on_disk(text, search_roots):
            return
        seen.add(ident)
        remaining.append(text)

    for raw in _listed_job_outputs(job):
        _keep(raw)
    prefix = _posix(job.get("output_prefix")).replace("\\", "/")
    if prefix:
        stem = Path(prefix).name
        parent_rel = Path(prefix).parent.as_posix()
        for root in search_roots:
            parent = _safe_under(root, parent_rel) if parent_rel not in {"", "."} else Path(root)
            if parent is None or not parent.is_dir():
                continue
            try:
                hits = list(parent.glob(f"{stem}*"))
            except OSError:
                continue
            for hit in hits:
                if not hit.is_file() or hit.suffix.lower() not in _VIDEO_SUFFIXES:
                    continue
                _keep(str(hit))
                rel = _canonical_relpath(hit.as_posix())
                if rel:
                    _keep(rel)
    return remaining


def _job_sidecar_paths(job_path: Path, job: Dict[str, Any]) -> List[Path]:
    name = job_path.name
    base = name[: -len(".job.json")] if name.endswith(".job.json") else job_path.stem.replace(".job", "")
    parent = job_path.parent
    out: List[Path] = []
    for suffix in (".prompt.json", ".submit.json", ".timings.json", ".workflow.json"):
        cand = parent / f"{base}{suffix}"
        if cand.is_file():
            out.append(cand)
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    for key in ("submit_path", "prompt_path"):
        raw = str(submit.get(key) or "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser()
        if p.is_file() and p not in out and p != job_path:
            out.append(p)
    raw_wf = str(job.get("generated_workflow_path") or "").strip()
    if raw_wf:
        p = Path(raw_wf).expanduser()
        if p.is_file() and p not in out and p != job_path:
            out.append(p)
    return out


def _rename_discarded(path: Path) -> Path:
    dest = Path(str(path) + ".discarded")
    n = 1
    while dest.exists():
        dest = Path(f"{path}.discarded.{n}")
        n += 1
    path.rename(dest)
    return dest


def _write_job_json(path: Path, job: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _strip_purged_outputs(job: Dict[str, Any], purged_rel: str) -> None:
    purged_keys = _expanded_path_keys(purged_rel)

    def _filter(src: Any) -> Any:
        if not isinstance(src, list):
            return src
        kept: List[Any] = []
        for item in src:
            if _expanded_path_keys(item) & purged_keys:
                continue
            kept.append(item)
        return kept

    submit = job.get("submit") if isinstance(job.get("submit"), dict) else None
    if submit is not None:
        if "outputs" in submit:
            submit["outputs"] = _filter(submit.get("outputs"))
    deposit = job.get("deposit") if isinstance(job.get("deposit"), dict) else None
    if deposit is not None and "videos" in deposit:
        deposit["videos"] = _filter(deposit.get("videos"))


def _retire_producing_job(
    job_path: Path,
    purged_rel: str,
    search_roots: Sequence[Path],
) -> Dict[str, Any]:
    if not job_path.is_file():
        return {"ok": False, "error": "job_missing", "job_path": str(job_path)}
    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": "job_unreadable", "job_path": str(job_path), "detail": str(exc)}
    if not isinstance(job, dict):
        return {"ok": False, "error": "job_invalid", "job_path": str(job_path)}
    key = str(job.get("job_key") or job_path.stem.replace(".job", ""))
    remaining = _remaining_job_videos(job, purged_rel, search_roots)
    if remaining:
        _strip_purged_outputs(job, purged_rel)
        _write_job_json(job_path, job)
        return {
            "ok": True,
            "job_key": key,
            "job_path": str(job_path),
            "action": "stripped",
            "remaining": remaining,
        }
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else None
    if not isinstance(submit, dict):
        submit = {}
        job["submit"] = submit
    submit["discarded"] = True
    submit["discarded_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    submit["discard_reason"] = "appetite_remove_purge"
    _write_job_json(job_path, job)
    renamed: List[str] = []
    for side in _job_sidecar_paths(job_path, job):
        try:
            renamed.append(str(_rename_discarded(side)))
        except OSError:
            continue
    archived = _rename_discarded(job_path)
    renamed.append(str(archived))
    return {
        "ok": True,
        "job_key": key,
        "job_path": str(archived),
        "action": "archived",
        "renamed": renamed,
    }


def purge_remove_asset(
    relpath: str,
    *,
    appetite_index_path: Path,
    jobs_dir: Path,
    pools_root: Path,
    search_roots: Sequence[Path],
) -> Dict[str, Any]:
    """
    Delete a remove-marked asset that nothing else depends on.

    Refuses when the asset is used as a job source or pool member. Deletes the
    media file, same-stem sidecars (XMP / thumb / trims), and rating/appetite rows.
    Producing jobs with no remaining videos are archived (``.discarded``); jobs
    that still have other outputs keep their records with this path stripped.
    """
    rel = _canonical_relpath(relpath) or _posix(relpath)
    if not rel:
        return {"ok": False, "error": "missing_relpath"}
    appetite_doc = load_appetite_doc(appetite_index_path)
    state = path_appetite_state(relpath, appetite_doc) or path_appetite_state(rel, appetite_doc)
    if normalize_appetite(state) != "remove":
        return {"ok": False, "error": "not_remove", "relpath": rel}
    as_output, as_source = _index_jobs(jobs_dir)
    pools = _index_pools(pools_root)
    analysis = analyze_remove_item(rel, as_output=as_output, as_source=as_source, pools=pools)
    if not analysis.get("purge_ready"):
        return {
            "ok": False,
            "error": "has_references",
            "relpath": rel,
            "blockers": analysis.get("blockers") or [],
            "counts": analysis.get("counts"),
        }
    deleted: List[str] = []
    missing: List[str] = []
    media_hits = resolve_media_files(relpath, search_roots) or resolve_media_files(rel, search_roots)
    if not media_hits:
        missing.append(rel)
    for media in media_hits:
        for sidecar in sidecar_files_for_media(media):
            if _unlink(sidecar):
                deleted.append(str(sidecar))
        if _unlink(media):
            deleted.append(str(media))
        else:
            missing.append(str(media))
    judgment = delete_judgment_for_relpath(appetite_index_path, relpath)
    extra = delete_judgment_for_relpath(appetite_index_path, rel)
    retired: List[Dict[str, Any]] = []
    for rec in analysis.get("as_output_jobs") or []:
        raw_path = str(rec.get("job_path") or "").strip()
        if not raw_path:
            continue
        retired.append(_retire_producing_job(Path(raw_path), rel, search_roots))
    return {
        "ok": True,
        "relpath": rel,
        "deleted_files": deleted,
        "missing_files": missing,
        "appetite_rows": int(judgment.get("appetite_rows") or 0) + int(extra.get("appetite_rows") or 0),
        "rating_rows": int(judgment.get("rating_rows") or 0) + int(extra.get("rating_rows") or 0),
        "producing_jobs": retired,
        "deletion": "purged",
    }


def purge_remove_assets(
    relpaths: Sequence[str],
    **kwargs: Any,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for raw in relpaths:
        results.append(purge_remove_asset(str(raw), **kwargs))
    purged = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    return {
        "ok": not failed,
        "purged": len(purged),
        "failed": len(failed),
        "results": results,
        "deletion": "ready_only",
    }
