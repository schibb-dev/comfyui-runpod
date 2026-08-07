#!/usr/bin/env python3
"""Identity-still candidates + mint for shapes that require an image slot.

Two entry points share one ranking ladder:
  A) still-first — starter LoadImage / job binding already known
  B) video-first — walk parent_output / embedded prompt lineage back to a still

Last resort: mint the first frame of the earliest ancestor video (or this clip).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shape_factory_queue import (
    _image_source_slots,
    _infer_still_from_media,
    _resolve_still_file,
)

EVIDENCE_RANK = {
    "lineage_root": 0,
    "job_binding": 1,
    "ancestor_opener": 2,
    "rated_opener": 3,
    "minted": 4,
    "first_frame": 5,
    "body": 0,
    "embedded_load_image": 0,
    "png_load_image": 0,
    "mp4_load_image": 0,
}

_OPENER_FAMILY_HINTS = ("faceblast", "kneel", "fb9-faceblast", "fb9_kneel", "fb9-kneel")
_MINT_PREFIX = "IDM"
_MAX_CANDIDATES = 12
_MAX_LINEAGE_HOPS = 8
_MAX_RATED = 6


def shape_needs_identity_still(shape: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(shape, dict):
        return False
    return bool(_image_source_slots(shape))


def load_shape_for_family(data_root: Path, family_slug: str) -> Optional[Dict[str, Any]]:
    slug = str(family_slug or "").strip()
    if not slug:
        return None
    path = Path(data_root) / "shapes" / f"{slug}.shape.yaml"
    if not path.is_file():
        return None
    try:
        from shape_factory import load_yaml

        doc = load_yaml(path)
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def _norm(p: str) -> str:
    return str(p or "").strip().replace("\\", "/").rstrip("/")


def _candidate_id(path: str) -> str:
    return hashlib.sha1(_norm(path).encode("utf-8")).hexdigest()[:16]


def _files_rel_for_still(path: Path, *, workspace_root: Path) -> Optional[str]:
    """Prefer workspace-relative ``input/<bn>`` when the file is visible there."""
    bn = path.name
    if not bn:
        return None
    ws_root = Path(workspace_root).expanduser().resolve()
    ws_input = ws_root / "input" / bn
    try:
        if path.resolve() == ws_input.resolve() or ws_input.is_file():
            return f"input/{bn}"
    except OSError:
        if ws_input.is_file():
            return f"input/{bn}"
    try:
        rel = path.resolve().relative_to(ws_root)
        return str(rel).replace("\\", "/")
    except ValueError:
        pass
    return f"input/{bn}"


def _still_row(
    abs_path: Path,
    *,
    evidence: str,
    label: str,
    workspace_root: Path,
    lineage_depth: Optional[int] = None,
    source_video_relpath: Optional[str] = None,
) -> Dict[str, Any]:
    rel = _files_rel_for_still(abs_path, workspace_root=workspace_root) or abs_path.name
    url = "/files/" + rel.replace("\\", "/")
    row: Dict[str, Any] = {
        "id": _candidate_id(str(abs_path)),
        "path": str(abs_path),
        "relpath": rel,
        "url": url,
        "thumb_url": url,
        "evidence": evidence,
        "label": label,
    }
    if lineage_depth is not None:
        row["lineage_depth"] = int(lineage_depth)
    if source_video_relpath:
        row["source_video_relpath"] = source_video_relpath
    return row


def _binding_still_paths(job: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(job, dict):
        return []
    bindings = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    out: List[str] = []
    for slot in ("identity_anchor", "source_still"):
        raw = bindings.get(slot)
        if isinstance(raw, str) and raw.strip():
            out.append(raw.strip())
        elif isinstance(raw, dict):
            p = str(raw.get("path") or "").strip()
            if p:
                out.append(p)
    return out


def _job_family(job: Optional[Dict[str, Any]], index_row: Optional[Dict[str, Any]] = None) -> str:
    if isinstance(job, dict):
        fam = str(job.get("family_slug") or "").strip()
        if fam:
            return fam
    if isinstance(index_row, dict):
        return str(index_row.get("family_slug") or "").strip()
    return ""


def _is_opener_family(family_slug: str) -> bool:
    s = str(family_slug or "").strip().lower().replace("_", "-")
    return any(h in s for h in _OPENER_FAMILY_HINTS)


def _relpath_from_abs(abs_path: str, output_root: Path) -> str:
    rel = _norm(abs_path)
    out = str(Path(output_root).expanduser().resolve()).replace("\\", "/")
    for prefix in (out + "/", str(output_root).replace("\\", "/") + "/"):
        if rel.startswith(prefix):
            rel = rel[len(prefix) :]
            break
    if not rel.startswith("og/") and "/og/" in rel:
        rel = "og/" + rel.split("/og/", 1)[1]
    return rel


def _resolve_media_abs(
    raw: str,
    *,
    workspace_root: Path,
    output_root: Path,
    data_root: Path,
) -> Optional[Path]:
    s = _norm(raw)
    if not s:
        return None
    p = Path(s).expanduser()
    if p.is_file():
        return p.resolve()
    try:
        from shape_factory_map import resolve_existing_path

        return resolve_existing_path(
            s,
            output_root=output_root,
            data_root=data_root,
            workspace_root=workspace_root,
        )
    except Exception:
        return None


def walk_lineage_videos(
    *,
    start_relpath: str,
    start_abs: str,
    job: Optional[Dict[str, Any]],
    output_root: Path,
    data_root: Path,
    workspace_root: Path,
    max_hops: int = _MAX_LINEAGE_HOPS,
) -> List[Dict[str, Any]]:
    """
    Nearest-first chain of videos: this clip → parents via job / job_output_index.

    Each hop: ``{relpath, abs_path, depth, job_key, family_slug, parent_output}``.
    """
    hops: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def add(rel: str, abs_path: str, depth: int, meta: Optional[Dict[str, Any]] = None) -> None:
        key = _norm(rel) or _norm(abs_path)
        if not key or key in seen:
            return
        seen.add(key)
        row = {
            "relpath": _norm(rel) or _relpath_from_abs(abs_path, output_root),
            "abs_path": _norm(abs_path),
            "depth": depth,
            "job_key": str((meta or {}).get("job_key") or ""),
            "family_slug": str((meta or {}).get("family_slug") or ""),
            "parent_output": str((meta or {}).get("parent_output") or ""),
        }
        hops.append(row)

    start_rel = _norm(start_relpath) or _relpath_from_abs(start_abs, output_root)
    start_meta: Dict[str, Any] = {}
    if isinstance(job, dict):
        start_meta = {
            "job_key": str(job.get("job_key") or ""),
            "family_slug": str(job.get("family_slug") or ""),
            "parent_output": str(job.get("parent_output") or "")
            or str((job.get("construction") or {}).get("parent_output") or ""),
        }
    add(start_rel, start_abs, 0, start_meta)

    idx_con = None
    try:
        from shape_factory_job_output_index import (
            default_job_output_index_path,
            lookup_by_relpath,
            open_job_output_index,
        )

        og_guess = Path(output_root) / "og"
        idx_path = default_job_output_index_path(og_guess if og_guess.is_dir() else Path(output_root))
        if idx_path.is_file():
            idx_con = open_job_output_index(idx_path)
    except Exception:
        idx_con = None

    try:
        i = 0
        while i < len(hops) and hops[i]["depth"] < max_hops:
            cur = hops[i]
            i += 1
            parents: List[str] = []
            po = str(cur.get("parent_output") or "").strip()
            if po:
                parents.append(po)
            if idx_con is not None:
                try:
                    row = lookup_by_relpath(idx_con, cur["relpath"], output_root=Path(output_root))
                except Exception:
                    row = None
                if isinstance(row, dict):
                    if not cur.get("job_key"):
                        cur["job_key"] = str(row.get("job_key") or "")
                    if not cur.get("family_slug"):
                        cur["family_slug"] = str(row.get("family_slug") or "")
                    po2 = str(row.get("parent_output") or "").strip()
                    if po2:
                        parents.append(po2)
            # Deduplicate parents
            seen_p: set[str] = set()
            for raw in parents:
                n = _norm(raw)
                if not n or n in seen_p:
                    continue
                seen_p.add(n)
                abs_p = _resolve_media_abs(
                    n,
                    workspace_root=workspace_root,
                    output_root=output_root,
                    data_root=data_root,
                )
                meta: Dict[str, Any] = {}
                if idx_con is not None:
                    try:
                        prow = lookup_by_relpath(
                            idx_con,
                            _relpath_from_abs(str(abs_p) if abs_p else n, output_root),
                            output_root=Path(output_root),
                        )
                        if isinstance(prow, dict):
                            meta = prow
                    except Exception:
                        meta = {}
                add(
                    _relpath_from_abs(str(abs_p) if abs_p else n, output_root),
                    str(abs_p) if abs_p else n,
                    int(cur["depth"]) + 1,
                    meta,
                )
    finally:
        if idx_con is not None:
            try:
                idx_con.close()
            except Exception:
                pass

    return hops


def _find_job(data_root: Path, job_key: str) -> Optional[Dict[str, Any]]:
    key = str(job_key or "").strip()
    if not key:
        return None
    try:
        from shape_factory_queue import _find_job_doc

        found = _find_job_doc(Path(data_root), key)
        if found:
            return found[0]
    except Exception:
        pass
    return None


def _collect_rated_opener_stills(
    *,
    workspace_root: Path,
    output_root: Path,
    data_root: Path,
    limit: int = _MAX_RATED,
) -> List[Dict[str, Any]]:
    """Best-effort: high-rated FaceBlast/Kneel outputs with recoverable stills."""
    out: List[Dict[str, Any]] = []
    try:
        from shape_factory_job_output_index import (
            default_job_output_index_path,
            open_job_output_index,
        )
        from shape_factory_ratings import (
            default_ratings_db_path,
            open_ratings_db,
        )
    except ImportError:
        return out

    og = Path(output_root) / "og"
    og_root = og if og.is_dir() else Path(output_root)
    idx_path = default_job_output_index_path(og_root)
    ratings_path = default_ratings_db_path(og_root)
    if not idx_path.is_file() or not ratings_path.is_file():
        return out

    try:
        rcon = open_ratings_db(ratings_path)
        icon = open_job_output_index(idx_path)
    except Exception:
        return out

    try:
        # High explicit stars; join by basename / relpath heuristics.
        rows = rcon.execute(
            "SELECT asset_key, explicit FROM rating_row WHERE explicit IS NOT NULL AND explicit >= 4 "
            "ORDER BY explicit DESC, updated_at DESC LIMIT 80"
        ).fetchall()
        for r in rows:
            if len(out) >= limit:
                break
            asset_key = str(r["asset_key"] if hasattr(r, "keys") else r[0] or "").replace("\\", "/")
            if not asset_key:
                continue
            bn = Path(asset_key).name
            jrow = icon.execute(
                "SELECT * FROM job_output WHERE output_relpath = ? OR output_basename = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (asset_key if asset_key.startswith("og/") else f"og/{asset_key}", bn),
            ).fetchone()
            if jrow is None:
                continue
            fam = str(jrow["family_slug"] or "")
            if not _is_opener_family(fam):
                continue
            job = _find_job(data_root, str(jrow["job_key"] or ""))
            for raw in _binding_still_paths(job):
                resolved = _resolve_still_file(
                    raw,
                    workspace_root=workspace_root,
                    output_root=output_root,
                    data_root=data_root,
                )
                if resolved is None:
                    continue
                out.append(
                    _still_row(
                        resolved,
                        evidence="rated_opener",
                        label=f"Rated opener · {fam}",
                        workspace_root=workspace_root,
                    )
                )
                break
            if not _binding_still_paths(job):
                inferred = _infer_still_from_media(
                    str(jrow["output_relpath"] or ""),
                    workspace_root=workspace_root,
                    output_root=output_root,
                    data_root=data_root,
                )
                if inferred:
                    still_path, _ev = inferred
                    out.append(
                        _still_row(
                            Path(still_path),
                            evidence="rated_opener",
                            label=f"Rated opener · {fam}",
                            workspace_root=workspace_root,
                        )
                    )
    except Exception:
        return out
    finally:
        try:
            rcon.close()
        except Exception:
            pass
        try:
            icon.close()
        except Exception:
            pass
    return out


def list_identity_still_candidates(
    *,
    relpath: str,
    family_slug: str = "",
    job_key: str = "",
    workspace_root: Path,
    output_root: Path,
    data_root: Path,
    media_abs: Optional[Path] = None,
    job: Optional[Dict[str, Any]] = None,
    shape: Optional[Dict[str, Any]] = None,
    include_rated: bool = True,
    max_candidates: int = _MAX_CANDIDATES,
) -> Dict[str, Any]:
    """
    Ranked identity-still candidates + mint targets for Workbench Extend.

    Lazy first-frame: listed under ``mint_targets`` (not pre-extracted).
    """
    fam = str(family_slug or "").strip()
    shape_doc = shape if isinstance(shape, dict) else load_shape_for_family(data_root, fam) if fam else None
    needed_slots = _image_source_slots(shape_doc) if shape_doc else []
    needed = bool(needed_slots)
    if fam and shape_doc is None:
        # Unknown family — still allow browsing candidates (needed unknown).
        needed = True
        needed_slots = ["identity_anchor"]

    if not needed:
        return {
            "ok": True,
            "needed": False,
            "slots": [],
            "recommended_id": None,
            "candidates": [],
            "mint_targets": [],
            "lineage_summary": [],
        }

    rel = _norm(relpath)
    abs_media = media_abs
    if abs_media is None or not Path(abs_media).is_file():
        abs_media = _resolve_media_abs(
            rel,
            workspace_root=workspace_root,
            output_root=output_root,
            data_root=data_root,
        )
    abs_s = str(abs_media) if abs_media else ""

    job_doc = job
    if job_doc is None and job_key:
        job_doc = _find_job(data_root, job_key)

    hops = walk_lineage_videos(
        start_relpath=rel,
        start_abs=abs_s,
        job=job_doc,
        output_root=output_root,
        data_root=data_root,
        workspace_root=workspace_root,
    )

    candidates: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()

    def push(row: Dict[str, Any]) -> None:
        p = _norm(str(row.get("path") or ""))
        if not p or p in seen_paths:
            return
        if not Path(p).is_file():
            return
        seen_paths.add(p)
        candidates.append(row)

    # 1–2: job bindings (still-first) + lineage LoadImage per hop (nearest → root)
    for raw in _binding_still_paths(job_doc):
        resolved = _resolve_still_file(
            raw,
            workspace_root=workspace_root,
            output_root=output_root,
            data_root=data_root,
        )
        if resolved is not None:
            push(
                _still_row(
                    resolved,
                    evidence="job_binding",
                    label="Job binding",
                    workspace_root=workspace_root,
                    lineage_depth=0,
                )
            )

    for hop in hops:
        media = hop.get("abs_path") or hop.get("relpath") or ""
        inferred = _infer_still_from_media(
            str(media),
            workspace_root=workspace_root,
            output_root=output_root,
            data_root=data_root,
        )
        if not inferred:
            continue
        still_path, ev = inferred
        depth = int(hop.get("depth") or 0)
        # Deepest recovered still ≈ lineage root preference when ranking
        is_rootish = depth == max((h.get("depth") or 0) for h in hops) if hops else depth == 0
        fam_h = str(hop.get("family_slug") or "")
        if _is_opener_family(fam_h):
            evidence = "ancestor_opener"
            label = f"Opener still · {fam_h}"
        elif is_rootish or depth > 0:
            evidence = "lineage_root" if is_rootish else "ancestor_opener"
            label = f"Lineage still · depth {depth}"
        else:
            evidence = "lineage_root" if ev else "lineage_root"
            label = "Embedded LoadImage"
        push(
            _still_row(
                Path(still_path),
                evidence=evidence,
                label=label,
                workspace_root=workspace_root,
                lineage_depth=depth,
                source_video_relpath=str(hop.get("relpath") or "") or None,
            )
        )

        # Also pull opener job bindings at this hop
        jk = str(hop.get("job_key") or "")
        if jk and jk != str((job_doc or {}).get("job_key") or ""):
            hop_job = _find_job(data_root, jk)
            for raw in _binding_still_paths(hop_job):
                resolved = _resolve_still_file(
                    raw,
                    workspace_root=workspace_root,
                    output_root=output_root,
                    data_root=data_root,
                )
                if resolved is not None:
                    push(
                        _still_row(
                            resolved,
                            evidence="ancestor_opener" if _is_opener_family(_job_family(hop_job, hop)) else "job_binding",
                            label=f"Ancestor binding · {jk[:40]}",
                            workspace_root=workspace_root,
                            lineage_depth=depth,
                        )
                    )

    # Prior minted frames under input/
    input_roots = [
        Path("/home/yuji/comfyui-runpod-data/input"),
        Path(workspace_root) / "input",
        Path(data_root) / "input",
    ]
    for root in input_roots:
        if not root.is_dir():
            continue
        try:
            for p in sorted(root.glob(f"{_MINT_PREFIX}*.jpeg"))[:20]:
                push(
                    _still_row(
                        p,
                        evidence="minted",
                        label=f"Minted · {p.name[:20]}",
                        workspace_root=workspace_root,
                    )
                )
        except OSError:
            pass
        break

    if include_rated:
        for row in _collect_rated_opener_stills(
            workspace_root=workspace_root,
            output_root=output_root,
            data_root=data_root,
        ):
            push(row)

    def sort_key(row: Dict[str, Any]) -> Tuple[int, int, str]:
        ev = str(row.get("evidence") or "")
        rank = EVIDENCE_RANK.get(ev, 50)
        # Prefer deeper lineage for lineage_root (closer to starter)
        depth = int(row.get("lineage_depth") or 0)
        depth_score = -depth if ev in ("lineage_root", "ancestor_opener") else depth
        return (rank, depth_score, str(row.get("path") or ""))

    candidates.sort(key=sort_key)
    candidates = candidates[: max(1, int(max_candidates))]

    # Mint targets: earliest ancestor first, then this clip — videos lacking a recovered still
    mint_targets: List[Dict[str, Any]] = []
    seen_mint: set[str] = set()
    # Prefer root (highest depth) then walk back to current
    for hop in sorted(hops, key=lambda h: -int(h.get("depth") or 0)):
        rel_v = str(hop.get("relpath") or "")
        abs_v = str(hop.get("abs_path") or "")
        key = _norm(rel_v) or _norm(abs_v)
        if not key or key in seen_mint:
            continue
        path_ok = Path(abs_v).is_file() if abs_v else False
        if not path_ok:
            resolved = _resolve_media_abs(
                rel_v or abs_v,
                workspace_root=workspace_root,
                output_root=output_root,
                data_root=data_root,
            )
            if resolved is None:
                continue
            abs_v = str(resolved)
            path_ok = resolved.is_file()
        if not path_ok:
            continue
        # Skip if this hop already yielded a still candidate from embedded prompt
        hop_has_still = any(
            str(c.get("source_video_relpath") or "") == rel_v for c in candidates if c.get("source_video_relpath")
        )
        evidence = "first_frame"
        label = (
            f"First frame · root ({hop.get('family_slug') or 'video'})"
            if int(hop.get("depth") or 0) == max((h.get("depth") or 0) for h in hops)
            else f"First frame · depth {hop.get('depth')}"
        )
        if hop_has_still and int(hop.get("depth") or 0) > 0:
            # Still useful as override; keep but lower priority via ordering
            label = f"First frame (alt) · {Path(rel_v).name[:28]}"
        seen_mint.add(key)
        mint_targets.append(
            {
                "video_relpath": rel_v,
                "video_path": abs_v,
                "at": "start",
                "evidence": evidence,
                "label": label,
                "lineage_depth": int(hop.get("depth") or 0),
                "family_slug": str(hop.get("family_slug") or ""),
            }
        )

    recommended_id = candidates[0]["id"] if candidates else None

    lineage_summary = [
        {
            "relpath": h.get("relpath"),
            "depth": h.get("depth"),
            "family_slug": h.get("family_slug") or None,
            "job_key": h.get("job_key") or None,
        }
        for h in hops
    ]

    return {
        "ok": True,
        "needed": True,
        "slots": needed_slots,
        "recommended_id": recommended_id,
        "candidates": candidates,
        "mint_targets": mint_targets[:8],
        "lineage_summary": lineage_summary,
        "family_slug": fam or None,
        "relpath": rel,
    }


def default_mint_input_dir(*, workspace_root: Path, data_root: Path) -> Path:
    host = Path("/home/yuji/comfyui-runpod-data/input")
    if host.is_dir():
        return host
    ws = Path(workspace_root).expanduser().resolve() / "input"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def mint_identity_still_from_video(
    *,
    video_path: str = "",
    video_relpath: str = "",
    at: str = "start",
    workspace_root: Path,
    output_root: Path,
    data_root: Path,
    ffmpeg: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract one frame to a content-addressed jpeg under input/.

    ``at``: ``start`` | ``end`` | float/int seconds as string.
    """
    ff = ffmpeg or shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg_not_found")

    abs_v = _resolve_media_abs(
        video_path or video_relpath,
        workspace_root=workspace_root,
        output_root=output_root,
        data_root=data_root,
    )
    if abs_v is None or not abs_v.is_file():
        raise FileNotFoundError(video_relpath or video_path or "video")

    at_s = str(at or "start").strip().lower()
    ss_args: List[str] = []
    if at_s in ("start", "0", "0.0", ""):
        ss_args = ["-ss", "0"]
    elif at_s == "end":
        # Seek near end via ffprobe duration when possible
        dur = _ffprobe_duration(abs_v)
        if dur is not None and dur > 0.05:
            ss_args = ["-ss", f"{max(0.0, dur - 0.05):.3f}"]
        else:
            ss_args = ["-sseof", "-0.05"]
    else:
        try:
            sec = float(at_s)
        except ValueError as e:
            raise ValueError(f"bad_at: {at}") from e
        ss_args = ["-ss", f"{max(0.0, sec):.3f}"]

    with tempfile.TemporaryDirectory(prefix="idstill_") as td:
        tmp_jpg = Path(td) / "frame.jpg"
        cmd = [
            ff,
            "-hide_banner",
            "-loglevel",
            "error",
            *ss_args,
            "-i",
            str(abs_v),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(tmp_jpg),
        ]
        # For -sseof, place seek after -i
        if ss_args and ss_args[0] == "-sseof":
            cmd = [
                ff,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(abs_v),
                "-sseof",
                ss_args[1],
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(tmp_jpg),
            ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not tmp_jpg.is_file() or tmp_jpg.stat().st_size < 32:
            detail = (proc.stderr or proc.stdout or "").strip()[:400]
            raise RuntimeError(f"ffmpeg_frame_failed: {detail or proc.returncode}")

        data = tmp_jpg.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        name = f"{_MINT_PREFIX}{digest}.jpeg"
        out_dir = default_mint_input_dir(workspace_root=workspace_root, data_root=data_root)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / name
        if not dest.is_file():
            dest.write_bytes(data)
        # Mirror into workspace input when mint landed on host path
        ws_dest = Path(workspace_root).expanduser().resolve() / "input" / name
        if dest.resolve() != ws_dest.resolve():
            try:
                ws_dest.parent.mkdir(parents=True, exist_ok=True)
                if not ws_dest.is_file():
                    ws_dest.write_bytes(data)
            except OSError:
                pass

    evidence = "first_frame" if at_s in ("start", "0", "0.0", "") else "minted"
    row = _still_row(
        dest if dest.is_file() else ws_dest,
        evidence=evidence,
        label=f"Minted frame · {at_s}",
        workspace_root=workspace_root,
        source_video_relpath=_norm(video_relpath) or None,
    )
    return {
        "ok": True,
        "candidate": row,
        "sha256": digest,
        "at": at_s,
        "video_path": str(abs_v),
    }


def _ffprobe_duration(path: Path) -> Optional[float]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return None
        return float((proc.stdout or "").strip())
    except Exception:
        return None
