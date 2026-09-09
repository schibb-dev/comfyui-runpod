"""Job-owned prompt fork (V1): catalog is a seed; the job holds runtime truth.

See plan: Prompt request/order design — V1 treats each ``.job.json`` as an order
with inline ``job["prompt"]``. Macros / request-vs-order split come later.

Template edit + promote: instances carry ``content_hash``; library writes are
explicit fork/overwrite with provenance (see Template instance promote plan).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


class OwnedPromptFrozenError(RuntimeError):
    """Raised when mutating a prompt after execution start (cabin doors)."""


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def prompt_content_hash(positive: Any = "", negative: Any = "") -> str:
    """Stable identity for prompt text (library + job pin)."""
    blob = f"{positive or ''}\n---\n{negative or ''}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def attach_content_hash(owned: Dict[str, Any]) -> Dict[str, Any]:
    owned["content_hash"] = prompt_content_hash(owned.get("positive"), owned.get("negative"))
    return owned


def fork_owned_prompt(
    *,
    positive: Any = "",
    negative: Any = "",
    label: Any = None,
    name: Any = None,
    slug: Any = None,
    source_profile: Optional[str] = None,
    frozen: bool = False,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "positive": str(positive or ""),
        "negative": str(negative or ""),
        "frozen": bool(frozen),
    }
    if label is not None and str(label).strip() != "":
        out["label"] = label
    display_name = str(name or "").strip()
    if display_name:
        out["name"] = display_name
    variant_slug = prompt_variant_slug(slug, name, label, source_profile)
    if variant_slug:
        out["slug"] = variant_slug
    if source_profile:
        out["source_profile"] = str(source_profile)
    attach_content_hash(out)
    return out


def fork_owned_prompt_from_profile_doc(
    doc: Dict[str, Any],
    *,
    source_profile: Optional[str] = None,
) -> Dict[str, Any]:
    return fork_owned_prompt(
        positive=doc.get("positive"),
        negative=doc.get("negative"),
        label=doc.get("label"),
        name=doc.get("name"),
        slug=doc.get("slug"),
        source_profile=source_profile,
        frozen=False,
    )


def is_scratch_prompt_path(raw: Any) -> bool:
    """True for compose-scratch / ``__draft_`` prompt files (not catalog)."""
    norm = str(raw or "").replace("\\", "/").lower()
    if not norm:
        return False
    name = Path(norm).name
    return "/_scratch/" in f"/{norm}" or "__draft_" in name


def _resolve_prompt_profile_path(
    raw: str,
    *,
    data_root: Optional[Path] = None,
) -> Optional[Path]:
    path = Path(str(raw or "").strip()).expanduser()
    if path.is_file():
        return path
    if data_root is not None and str(raw or "").strip():
        cand = Path(data_root).expanduser() / raw
        if cand.is_file():
            return cand
        try:
            from shape_factory_map import resolve_existing_path

            found = resolve_existing_path(
                raw,
                output_root=Path(data_root),
                data_root=Path(data_root),
                workspace_root=None,
            )
            if found.is_file():
                return found
        except Exception:
            pass
    return None


def _catalog_from_scratch_filename(
    path: Path,
    *,
    data_root: Optional[Path] = None,
) -> str:
    """Recover ``pools/<family>/prompts/<stem>.json`` from ``<stem>__draft_<ts>.json``."""
    stem = Path(path).stem
    base = stem.split("__draft_", 1)[0].strip()
    if not base or base == stem:
        return ""
    parts = str(path).replace("\\", "/").split("/")
    family = ""
    if "_scratch" in parts:
        idx = parts.index("_scratch")
        if idx + 1 < len(parts):
            family = parts[idx + 1]
    candidates: list[Path] = []
    if data_root is not None and family:
        candidates.append(Path(data_root).expanduser() / "pools" / family / "prompts" / f"{base}.json")
    if family:
        # Host vs container data roots often differ; try the scratch file's ancestor.
        for parent in Path(path).resolve().parents:
            if parent.name in {"shape_factory", ".data"}:
                root = parent.parent if parent.name == "shape_factory" else parent
                candidates.append(root / "pools" / family / "prompts" / f"{base}.json")
                break
    for cand in candidates:
        if cand.is_file() and not is_scratch_prompt_path(str(cand)):
            return str(cand.resolve())
    return ""


def catalog_source_profile_from_path(
    source_profile: str,
    *,
    data_root: Optional[Path] = None,
) -> str:
    """Follow a scratch JSON's stamped catalog path; otherwise return the given path."""
    raw = str(source_profile or "").strip()
    if not raw:
        return ""
    path = _resolve_prompt_profile_path(raw, data_root=data_root)
    if path is None:
        return raw
    if not is_scratch_prompt_path(str(path)):
        return str(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        doc = None
    stamped = str((doc or {}).get("source_profile") or "").strip() if isinstance(doc, dict) else ""
    if stamped and not is_scratch_prompt_path(stamped):
        resolved = _resolve_prompt_profile_path(stamped, data_root=data_root)
        return str(resolved) if resolved is not None else stamped
    inferred = _catalog_from_scratch_filename(path, data_root=data_root)
    return inferred or str(path)


def fork_owned_prompt_from_profile_file(path: Path) -> Dict[str, Any]:
    p = Path(path).expanduser()
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"prompt profile is not a JSON object: {p}")
    src = str(p.resolve())
    stamped = str(doc.get("source_profile") or "").strip()
    if stamped and not is_scratch_prompt_path(stamped):
        src = stamped
    return fork_owned_prompt_from_profile_doc(doc, source_profile=src)


def get_owned_prompt(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = job.get("prompt") if isinstance(job, dict) else None
    if not isinstance(raw, dict):
        return None
    if "positive" not in raw and "negative" not in raw:
        return None
    return raw


def is_owned_prompt_frozen(job: Dict[str, Any]) -> bool:
    owned = get_owned_prompt(job)
    return bool(owned and owned.get("frozen"))


def freeze_owned_prompt(job: Dict[str, Any], *, at: Optional[str] = None) -> bool:
    """Mark owned prompt frozen. Returns True if this call newly froze it."""
    owned = get_owned_prompt(job)
    if owned is None:
        return False
    if owned.get("frozen"):
        return False
    attach_content_hash(owned)
    owned["frozen"] = True
    owned["frozen_at"] = at or utc_now_iso()
    job["prompt"] = owned
    return True


def ensure_owned_prompt_mutable(job: Dict[str, Any]) -> None:
    if is_owned_prompt_frozen(job):
        raise OwnedPromptFrozenError("owned prompt is frozen (execution started)")


def merge_owned_prompt(job: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Patch owned prompt fields; raises if frozen."""
    ensure_owned_prompt_mutable(job)
    owned = get_owned_prompt(job)
    if owned is None:
        owned = fork_owned_prompt()
    for key in ("positive", "negative", "label"):
        if key in override and override[key] is not None:
            owned[key] = override[key]
    owned["frozen"] = False
    attach_content_hash(owned)
    job["prompt"] = owned
    return owned


def ensure_owned_prompt_from_bindings(
    job: Dict[str, Any],
    *,
    data_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Return owned prompt, forking from ``bindings.prompt_profile`` when missing."""
    existing = get_owned_prompt(job)
    if existing is not None:
        if not existing.get("content_hash"):
            attach_content_hash(existing)
            job["prompt"] = existing
        return existing
    binds = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    meta = binds.get("prompt_profile") if isinstance(binds, dict) else None
    if not isinstance(meta, dict):
        return None
    raw = str(meta.get("path") or meta.get("relpath") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file() and data_root is not None:
        try:
            from shape_factory import resolve_job_asset_path

            path = resolve_job_asset_path(raw, data_root=Path(data_root))
        except Exception:
            return None
    if not path.is_file():
        return None
    try:
        owned = fork_owned_prompt_from_profile_file(path)
    except Exception:
        return None
    job["prompt"] = owned
    return owned


def profile_dict_for_apply(
    job: Dict[str, Any],
    *,
    asset_path: Optional[Path] = None,
    data_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Profile used when painting the Comfy API prompt.

    Prefer job-owned text; fall back to the binding file (legacy jobs).
    """
    owned = get_owned_prompt(job) or ensure_owned_prompt_from_bindings(job, data_root=data_root)
    if owned is not None:
        return {
            "positive": str(owned.get("positive") or ""),
            "negative": str(owned.get("negative") or ""),
            "label": owned.get("label"),
        }
    if asset_path is None:
        raise RuntimeError("no owned prompt and no profile asset path")
    doc = json.loads(Path(asset_path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise RuntimeError(f"prompt profile is not JSON object: {asset_path}")
    return doc


def owned_prompt_to_excerpt(
    owned: Dict[str, Any],
    *,
    max_chars: int = 280,
    data_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Shape compatible with ``_prompt_excerpt`` / Workbench PromptPeek."""
    from shape_factory_work_products import decode_prompt_markup

    positive = str(owned.get("positive") or "")
    negative = str(owned.get("negative") or "")
    source = str(owned.get("source_profile") or "").strip()
    basename = Path(source).name if source else str(owned.get("label") or "owned-prompt")
    ch = str(owned.get("content_hash") or "").strip() or prompt_content_hash(positive, negative)
    out: Dict[str, Any] = {
        "path": source or None,
        "basename": basename,
        "label": owned.get("label") or basename,
        "positive": positive,
        "negative": negative,
        "positive_rows": decode_prompt_markup(positive),
        "negative_rows": decode_prompt_markup(negative),
        "owned": True,
        "frozen": bool(owned.get("frozen")),
        "content_hash": ch,
        "snowflake": False,
    }
    if owned.get("frozen_at"):
        out["frozen_at"] = owned.get("frozen_at")
    if positive:
        out["positive_excerpt"] = positive if len(positive) <= max_chars else positive[: max_chars - 1] + "…"
        out["positive_chars"] = len(positive)
    if negative:
        out["negative_excerpt"] = negative if len(negative) <= 120 else negative[:119] + "…"
        out["negative_chars"] = len(negative)

    catalog = catalog_source_profile_from_path(source, data_root=data_root) if source else ""
    seed = _seed_baseline_from_source_profile(catalog or source, data_root=data_root)
    if seed is not None:
        out["seed"] = seed
        seed_hash = str(seed.get("content_hash") or "").strip()
        out["snowflake"] = bool(seed_hash and seed_hash != ch)
    # Compose scratch used to bind source_profile to the draft itself (same hash as
    # the owned text). Treat those as edited unless we can compare a real catalog.
    if is_scratch_prompt_path(source) and (not catalog or is_scratch_prompt_path(catalog)):
        out["snowflake"] = True
    display_name = str(owned.get("name") or "").strip()
    if not display_name and seed is not None:
        display_name = str(seed.get("name") or "").strip()
    if display_name:
        out["name"] = display_name
    variant_slug = prompt_variant_slug(
        owned.get("slug"),
        seed.get("slug") if seed else None,
        owned.get("label"),
        basename,
        source,
        seed.get("label") if seed else None,
        seed.get("basename") if seed else None,
        display_name,
    )
    if variant_slug:
        out["slug"] = variant_slug
    return out


def _seed_baseline_from_source_profile(
    source_profile: str,
    *,
    data_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Load seed template text from ``source_profile`` for snowflake comparison."""
    from shape_factory_work_products import decode_prompt_markup

    raw = str(source_profile or "").strip()
    if not raw:
        return None
    path = _resolve_prompt_profile_path(raw, data_root=data_root)
    if path is None or not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    positive = str(doc.get("positive") or "")
    negative = str(doc.get("negative") or "")
    seed: Dict[str, Any] = {
        "path": str(path),
        "label": doc.get("label") or path.stem,
        "basename": path.name,
        "positive": positive,
        "negative": negative,
        "positive_rows": decode_prompt_markup(positive),
        "negative_rows": decode_prompt_markup(negative),
        "content_hash": prompt_content_hash(positive, negative),
    }
    display_name = str(doc.get("name") or "").strip()
    if display_name:
        seed["name"] = display_name
    variant_slug = prompt_variant_slug(doc.get("slug"), doc.get("label"), path.stem, path.name, display_name)
    if variant_slug:
        seed["slug"] = variant_slug
    return seed


_SLUG_RE = re.compile(r"[^a-z0-9._-]+")
_SLUG_SEP_RE = re.compile(r"[_\s]+")
_SLUG_DASH_RE = re.compile(r"-{2,}")
_VARIANT_WRAPPERS = ("pp-catalog-", "catalog-", "prompt_profile-", "pp-")
_RESERVED_VARIANT_SLUGS = frozenset({"default", "catalog-default"})


def slugify_variant_label(label: str, *, fallback: str = "variant", reserved: bool = True) -> str:
    """Kebab slug for a variant. Guided by catalog-/pp- file stems; not limited to them."""
    raw = str(label or "").strip() or fallback
    raw = _SLUG_SEP_RE.sub("-", raw)
    slug = _SLUG_RE.sub("-", raw.lower()).strip("-._")
    slug = _SLUG_DASH_RE.sub("-", slug)
    if not slug:
        slug = fallback
    if reserved and slug in _RESERVED_VARIANT_SLUGS:
        slug = f"{fallback}-{utc_now_iso().replace(':', '').replace('-', '')[:15]}"
    return slug[:80]


def prompt_variant_slug(*candidates: Any, fallback: str = "") -> str:
    """Canonical variant slug (`default`, `faceblast-extend`, or any kebab id)."""
    for cand in candidates:
        got = _coerce_variant_slug(cand)
        if got:
            return got
    return str(fallback or "").strip()


def _coerce_variant_slug(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, dict):
        seed = raw.get("seed") if isinstance(raw.get("seed"), dict) else {}
        return prompt_variant_slug(
            raw.get("slug"),
            seed.get("slug") if seed else None,
            raw.get("label"),
            raw.get("basename"),
            raw.get("path"),
            seed.get("label") if seed else None,
            seed.get("basename") if seed else None,
            raw.get("name"),
            seed.get("name") if seed else None,
        )
    text = str(raw).strip()
    if not text:
        return ""
    text = text.replace("\\", "/").rsplit("/", 1)[-1]
    if text.lower().endswith(".json"):
        text = text[:-5]
    lower = text.lower()
    for prefix in _VARIANT_WRAPPERS:
        if lower.startswith(prefix):
            text = text[len(prefix) :]
            break
    return slugify_variant_label(text, fallback="", reserved=False)


def family_prompts_dir(data_root: Path, family_slug: str) -> Path:
    return Path(data_root).expanduser().resolve() / "pools" / str(family_slug).strip() / "prompts"


def _atomic_write_json(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _bak_path(path: Path) -> Path:
    stamp = utc_now_iso().replace(":", "").replace("-", "")
    return path.with_name(f"{path.name}.bak.{stamp}")


def build_library_prompt_doc(
    *,
    positive: str,
    negative: str,
    label: str,
    name: Optional[str] = None,
    parent_path: Optional[str] = None,
    parent_variant_id: Optional[str] = None,
    promoted_from_job: Optional[str] = None,
    note: Optional[str] = None,
    variant_id: Optional[str] = None,
) -> Dict[str, Any]:
    vid = str(variant_id or "").strip() or str(uuid.uuid4())
    doc: Dict[str, Any] = {
        "label": label,
        "positive": str(positive or ""),
        "negative": str(negative or ""),
        "variant_id": vid,
        "content_hash": prompt_content_hash(positive, negative),
        "created_at": utc_now_iso(),
    }
    display_name = str(name or "").strip()
    if not display_name and str(label or "").strip() and not str(label).lower().startswith("catalog-"):
        display_name = str(label).strip()
    if display_name:
        doc["name"] = display_name
    variant_slug = prompt_variant_slug(doc.get("slug"), display_name, label)
    if variant_slug:
        doc["slug"] = variant_slug
    if parent_path:
        doc["parent_path"] = str(parent_path)
    if parent_variant_id:
        doc["parent_variant_id"] = str(parent_variant_id)
    if promoted_from_job:
        doc["promoted_from_job"] = str(promoted_from_job)
    if note and str(note).strip():
        doc["note"] = str(note).strip()
    return doc


def promote_prompt_to_library(
    *,
    data_root: Path,
    family_slug: str,
    positive: str,
    negative: str,
    mode: str = "fork",
    label: Optional[str] = None,
    note: Optional[str] = None,
    promoted_from_job: Optional[str] = None,
    parent_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Write a prompt profile into ``pools/<family>/prompts/``.

    ``mode=fork`` (default): new ``<slug>.json`` file.
    ``mode=overwrite``: replace ``catalog-default.json`` after ``.bak``.
    """
    family = str(family_slug or "").strip()
    if not family:
        return {"ok": False, "error": "missing_family"}
    prompts_dir = family_prompts_dir(data_root, family)
    mode_s = str(mode or "fork").strip().lower()
    if mode_s not in {"fork", "overwrite"}:
        return {"ok": False, "error": "bad_mode", "detail": "mode must be fork|overwrite"}

    parent_variant_id = None
    parent = str(parent_path or "").strip() or None
    if parent:
        try:
            pdoc = json.loads(Path(parent).expanduser().read_text(encoding="utf-8"))
            if isinstance(pdoc, dict) and pdoc.get("variant_id"):
                parent_variant_id = str(pdoc.get("variant_id"))
        except Exception:
            pass

    if mode_s == "overwrite":
        target = prompts_dir / "catalog-default.json"
        label_s = str(label or "catalog-default").strip() or "catalog-default"
        bak = None
        if target.is_file():
            bak = _bak_path(target)
            bak.write_bytes(target.read_bytes())
        doc = build_library_prompt_doc(
            positive=positive,
            negative=negative,
            label=label_s,
            parent_path=parent or (str(target.resolve()) if target.is_file() else None),
            parent_variant_id=parent_variant_id,
            promoted_from_job=promoted_from_job,
            note=note,
        )
        _atomic_write_json(target, doc)
        return {
            "ok": True,
            "mode": "overwrite",
            "path": str(target.resolve()),
            "bak_path": str(bak.resolve()) if bak else None,
            "doc": doc,
        }

    label_s = str(label or "").strip() or f"variant-{utc_now_iso()[:10]}"
    slug = slugify_variant_label(label_s, fallback="variant")
    target = prompts_dir / f"{slug}.json"
    n = 2
    while target.is_file():
        target = prompts_dir / f"{slug}-{n}.json"
        n += 1
    doc = build_library_prompt_doc(
        positive=positive,
        negative=negative,
        label=label_s,
        parent_path=parent,
        parent_variant_id=parent_variant_id,
        promoted_from_job=promoted_from_job,
        note=note,
    )
    _atomic_write_json(target, doc)
    return {
        "ok": True,
        "mode": "fork",
        "path": str(target.resolve()),
        "bak_path": None,
        "doc": doc,
    }


def resolve_prompt_parent_path(job: Dict[str, Any]) -> Optional[str]:
    owned = get_owned_prompt(job)
    raw = ""
    if owned and owned.get("source_profile"):
        raw = str(owned.get("source_profile"))
    else:
        binds = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
        meta = binds.get("prompt_profile") if isinstance(binds, dict) else None
        if isinstance(meta, dict):
            raw = str(meta.get("path") or meta.get("relpath") or "").strip()
    if not raw:
        return None
    catalog = catalog_source_profile_from_path(raw)
    return catalog or raw
