#!/usr/bin/env python3
"""Durable Submit / shape-factory queue attempt log (success + failure).

Stored as NDJSON next to the Comfy queue ledger so ops/agents can find recent
failures without grepping docker access logs. Failed submits often never create
a job — this ledger is the association key (family + bindings + attempt_id).
"""

from __future__ import annotations

import json
import re
import uuid
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ATTEMPTS_BASENAME = "shape_factory_submit_attempts.jsonl"
DEFAULT_TAIL = 80
MAX_DETAIL_CHARS = 1200

_PERM_RE = re.compile(r"Permission denied:\s*'([^']+)'", re.I)
_ERRNO13_RE = re.compile(r"\[Errno\s*13\][^\n]*?'([^']+)'", re.I)
_MEDIA_SLOTS = (
    "source_still",
    "source_image",
    "identity_still",
    "identity_anchor",
    "start_image",
    "source_video",
    "parent_video",
    "source",
)
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif)$", re.I)
_VIDEO_EXT_RE = re.compile(r"\.(mp4|webm|mov|mkv)$", re.I)


def attempts_path(status_dir: Path) -> Path:
    return Path(status_dir).expanduser().resolve() / ATTEMPTS_BASENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_attempt_id() -> str:
    return f"sa_{uuid.uuid4().hex[:12]}"


def _basename(path: Any) -> str:
    s = str(path or "").strip().replace("\\", "/")
    if not s:
        return ""
    return s.rsplit("/", 1)[-1]


def _binding_raw_path(spec: Any) -> str:
    if isinstance(spec, str):
        return spec.strip()
    if isinstance(spec, dict):
        return str(spec.get("relpath") or spec.get("path") or spec.get("basename") or spec.get("member") or "").strip()
    return ""


def as_workspace_media_relpath(raw: Any) -> Optional[str]:
    """Normalize a binding path into a workspace-relative media path for /files/."""
    s = str(raw or "").strip().replace("\\", "/")
    if not s:
        return None
    try:
        from input_still_catalog import strip_download_copy_suffix  # type: ignore

        s = strip_download_copy_suffix(s)
    except Exception:
        pass
    for prefix in ("input/", "og/", "wip/", "output/", "thumbs/"):
        idx = s.find(prefix)
        if idx >= 0:
            return s[idx:]
    base = s.rsplit("/", 1)[-1]
    if _IMAGE_EXT_RE.search(base):
        return f"input/{base}"
    return None


def extract_media_relpath(bindings: Any) -> Optional[str]:
    if not isinstance(bindings, dict):
        return None
    for slot in _MEDIA_SLOTS:
        if slot not in bindings:
            continue
        rel = as_workspace_media_relpath(_binding_raw_path(bindings.get(slot)))
        if rel:
            return rel
    for spec in bindings.values():
        rel = as_workspace_media_relpath(_binding_raw_path(spec))
        if rel:
            return rel
    return None


def thumb_url_for_relpath(relpath: Optional[str]) -> Optional[str]:
    rel = str(relpath or "").strip().replace("\\", "/")
    if not rel:
        return None
    # Prefer a still/poster for videos when a sibling .png is the usual convention.
    if _VIDEO_EXT_RE.search(rel):
        rel = _VIDEO_EXT_RE.sub(".png", rel)
    elif not _IMAGE_EXT_RE.search(rel):
        return None
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in rel.split("/") if part != "")
    return f"/files/{encoded}"


def summarize_bindings(bindings: Any) -> Dict[str, str]:
    """Map slot → basename (or short path) for logs / UI."""
    out: Dict[str, str] = {}
    if not isinstance(bindings, dict):
        return out
    for slot, spec in bindings.items():
        key = str(slot or "").strip()
        if not key:
            continue
        raw = _binding_raw_path(spec)
        out[key] = _basename(raw) or raw[:120]
    return out


def summarize_request_body(body: Any) -> Dict[str, Any]:
    body_d = body if isinstance(body, dict) else {}
    family = str(body_d.get("family_slug") or body_d.get("family") or "").strip()
    bindings_raw = body_d.get("bindings")
    bindings = summarize_bindings(bindings_raw)
    media_relpath = extract_media_relpath(bindings_raw)
    surface = str(body_d.get("source_surface") or body_d.get("surface") or "").strip() or None
    return {
        "family_slug": family or None,
        "bindings": bindings,
        "media_relpath": media_relpath,
        "source_surface": surface,
        "dry_run": bool(body_d.get("dry_run") or False),
        "front": bool(body_d.get("front") or False),
    }


def _extract_path_hint(detail: str) -> Optional[str]:
    for rx in (_PERM_RE, _ERRNO13_RE):
        m = rx.search(detail or "")
        if m:
            return m.group(1)
    return None


def classify_queue_exception(exc: BaseException) -> Dict[str, Any]:
    """Map an exception to a stable error code + operator hint."""
    detail = str(exc)
    exc_type = type(exc).__name__
    low = detail.lower()
    path_hint = _extract_path_hint(detail)

    if "quarantined" in low:
        return {
            "error": "workflow_quarantined",
            "detail": detail[:MAX_DETAIL_CHARS],
            "hint": "This family template is quarantined. Pick another I2V family or release it after review.",
            "exc_type": exc_type,
            "path_hint": path_hint,
        }

    if isinstance(exc, PermissionError) or "permission denied" in low or "[errno 13]" in low:
        hint = (
            "Cannot write a generated workflow/job file. "
            "The family output directory is often owned by root after a root-shell smoke — "
            "chown it to the Experiments UI user."
        )
        if path_hint:
            hint = f"{hint} Path: {path_hint}"
        return {
            "error": "permission_denied",
            "detail": detail[:MAX_DETAIL_CHARS],
            "hint": hint,
            "exc_type": exc_type,
            "path_hint": path_hint,
        }

    if isinstance(exc, FileNotFoundError) or "no such file" in low:
        return {
            "error": "not_found",
            "detail": detail[:MAX_DETAIL_CHARS],
            "hint": "A required still, video, profile, or template path is missing.",
            "exc_type": exc_type,
            "path_hint": path_hint,
        }

    if isinstance(exc, ValueError):
        code = "bad_request"
        hint = "Fix the Submit bindings (family / still / video / prompt) and retry."
        if "missing required bindings" in low:
            code = "missing_bindings"
            hint = "This family needs a different primary input (e.g. source_video) than the still you selected."
        elif "combo_key mismatch" in low:
            code = "combo_key_mismatch"
        return {
            "error": code,
            "detail": detail[:MAX_DETAIL_CHARS],
            "hint": hint,
            "exc_type": exc_type,
            "path_hint": path_hint,
        }

    if isinstance(exc, RuntimeError):
        return {
            "error": "shape_factory_queue_failed",
            "detail": detail[:MAX_DETAIL_CHARS],
            "hint": "Queue prep or Comfy submit failed. Check the detail and recent attempt log.",
            "exc_type": exc_type,
            "path_hint": path_hint,
        }

    return {
        "error": "shape_factory_queue_failed",
        "detail": detail[:MAX_DETAIL_CHARS],
        "hint": "Unexpected submit failure. See attempt log under experiments/_status.",
        "exc_type": exc_type,
        "path_hint": path_hint,
    }


def http_status_for_error(error: str, *, is_runtime: bool = False) -> int:
    if error == "workflow_quarantined":
        return 409
    if error in {"bad_request", "missing_bindings", "combo_key_mismatch"}:
        return 400
    if error == "not_found":
        return 404
    if error == "permission_denied":
        return 500
    if is_runtime:
        return 502
    return 500


def build_attempt_record(
    *,
    ok: bool,
    request_summary: Dict[str, Any],
    attempt_id: Optional[str] = None,
    http_status: Optional[int] = None,
    error: Optional[str] = None,
    detail: Optional[str] = None,
    hint: Optional[str] = None,
    exc_type: Optional[str] = None,
    path_hint: Optional[str] = None,
    job_key: Optional[str] = None,
    prompt_id: Optional[str] = None,
    combo_key: Optional[str] = None,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "schema_version": 1,
        "attempt_id": attempt_id or new_attempt_id(),
        "ts": _utc_now(),
        "ok": bool(ok),
        "family_slug": request_summary.get("family_slug"),
        "bindings": dict(request_summary.get("bindings") or {}),
        "source_surface": request_summary.get("source_surface"),
        "dry_run": bool(request_summary.get("dry_run") or False),
        "front": bool(request_summary.get("front") or False),
    }
    media_relpath = str(request_summary.get("media_relpath") or "").strip()
    if media_relpath:
        rec["media_relpath"] = media_relpath
        thumb = thumb_url_for_relpath(media_relpath)
        if thumb:
            rec["thumb_url"] = thumb
    if http_status is not None:
        rec["http_status"] = int(http_status)
    if error:
        rec["error"] = str(error)
    if detail:
        rec["detail"] = str(detail)[:MAX_DETAIL_CHARS]
    if hint:
        rec["hint"] = str(hint)
    if exc_type:
        rec["exc_type"] = str(exc_type)
    if path_hint:
        rec["path_hint"] = str(path_hint)
    if job_key:
        rec["job_key"] = str(job_key)
    if prompt_id:
        rec["prompt_id"] = str(prompt_id)
    if combo_key:
        rec["combo_key"] = str(combo_key)
    return rec


def append_attempt(path: Path, record: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return record


def read_attempts(
    path: Path,
    *,
    limit: int = DEFAULT_TAIL,
    errors_only: bool = False,
    family_slug: Optional[str] = None,
) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return []
    lim = max(1, min(500, int(limit)))
    # Efficient-ish: read whole file when modest; otherwise tail bytes.
    try:
        size = path.stat().st_size
    except OSError:
        return []
    try:
        if size <= 2_000_000:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            with path.open("rb") as f:
                f.seek(max(0, size - 512_000))
                chunk = f.read().decode("utf-8", errors="replace")
            lines = chunk.splitlines()
            if size > 512_000 and lines:
                lines = lines[1:]  # drop partial first line
    except OSError:
        return []

    fam = (family_slug or "").strip() or None
    out: List[Dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        if errors_only and obj.get("ok"):
            continue
        if fam and str(obj.get("family_slug") or "") != fam:
            continue
        out.append(obj)
        if len(out) >= lim:
            break
    return out


def error_response_body(record: Dict[str, Any]) -> Dict[str, Any]:
    """JSON body returned to the UI for a failed queue attempt."""
    return {
        "ok": False,
        "error": record.get("error") or "shape_factory_queue_failed",
        "detail": record.get("detail") or "",
        "hint": record.get("hint"),
        "attempt_id": record.get("attempt_id"),
        "family_slug": record.get("family_slug"),
        "bindings": record.get("bindings") or {},
        "exc_type": record.get("exc_type"),
        "path_hint": record.get("path_hint"),
        "ts": record.get("ts"),
    }


def record_queue_outcome(
    status_dir: Path,
    *,
    body: Any,
    ok: bool,
    exc: Optional[BaseException] = None,
    payload: Optional[Dict[str, Any]] = None,
    http_status: Optional[int] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Append one attempt. Returns (attempt_record, error_response_or_None)."""
    summary = summarize_request_body(body)
    path = attempts_path(status_dir)
    if ok:
        payload_d = payload if isinstance(payload, dict) else {}
        rec = build_attempt_record(
            ok=True,
            request_summary=summary,
            http_status=http_status or 200,
            job_key=str(payload_d.get("job_key") or "") or None,
            prompt_id=str(payload_d.get("prompt_id") or "") or None,
            combo_key=str(payload_d.get("combo_key") or "") or None,
        )
        try:
            append_attempt(path, rec)
        except Exception:
            pass
        return rec, None

    classified = classify_queue_exception(exc or RuntimeError("unknown"))
    status = http_status
    if status is None:
        status = http_status_for_error(
            str(classified["error"]),
            is_runtime=isinstance(exc, RuntimeError) and classified["error"] != "workflow_quarantined",
        )
    rec = build_attempt_record(
        ok=False,
        request_summary=summary,
        http_status=status,
        error=str(classified["error"]),
        detail=str(classified.get("detail") or ""),
        hint=str(classified.get("hint") or "") or None,
        exc_type=str(classified.get("exc_type") or "") or None,
        path_hint=str(classified.get("path_hint") or "") or None,
    )
    try:
        append_attempt(path, rec)
    except Exception:
        pass
    return rec, error_response_body(rec)


def enrich_attempt_item(record: Dict[str, Any]) -> Dict[str, Any]:
    """Attach media_relpath / thumb_url for UI (including legacy basename-only rows)."""
    item = dict(record)
    media = str(item.get("media_relpath") or "").strip().replace("\\", "/")
    if not media:
        media = extract_media_relpath(item.get("bindings")) or ""
        if media:
            item["media_relpath"] = media
    if media and not item.get("thumb_url"):
        thumb = thumb_url_for_relpath(media)
        if thumb:
            item["thumb_url"] = thumb
    return item


def list_attempts_payload(
    status_dir: Path,
    *,
    limit: int = DEFAULT_TAIL,
    errors_only: bool = False,
    family_slug: Optional[str] = None,
) -> Dict[str, Any]:
    path = attempts_path(status_dir)
    items = [enrich_attempt_item(it) for it in read_attempts(path, limit=limit, errors_only=errors_only, family_slug=family_slug)]
    return {
        "ok": True,
        "path": str(path),
        "count": len(items),
        "errors_only": bool(errors_only),
        "family_slug": family_slug,
        "items": items,
    }