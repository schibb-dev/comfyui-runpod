"""Job-owned prompt fork (V1): catalog is a seed; the job holds runtime truth.

See ``docs/VARIANT_MANAGEMENT.md`` (prompt application) and
``docs/CATALOG_IDENTITY.md`` (app-wide id / name / available / default designation).

V1 treats each ``.job.json`` as an order with inline ``job["prompt"]``.
Macros / request-vs-order split come later.

Template edit + promote: instances carry ``content_hash``; library writes are
explicit fork/update-by-id with provenance. Default is a designation
(``default_variant_id``), never a reserved variant name.
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
    variant_id: Optional[str] = None,
    variant_name: Optional[str] = None,
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
    vid = str(variant_id or "").strip()
    if vid:
        out["variant_id"] = vid
    # Name snapshot at fork time (historical display); prefer explicit snapshot.
    snap = str(variant_name or display_name or "").strip()
    if snap and snap.lower() not in {"default", "catalog-default"}:
        out["variant_name"] = snap
    elif snap:
        # Avoid recording "default" as the historical name.
        out["variant_name"] = "Base"
    attach_content_hash(out)
    return out


def fork_owned_prompt_from_profile_doc(
    doc: Dict[str, Any],
    *,
    source_profile: Optional[str] = None,
) -> Dict[str, Any]:
    display = human_variant_name(doc)
    return fork_owned_prompt(
        positive=doc.get("positive"),
        negative=doc.get("negative"),
        label=doc.get("label"),
        name=doc.get("name") or display,
        slug=doc.get("slug"),
        source_profile=source_profile,
        frozen=False,
        variant_id=str(doc.get("variant_id") or "").strip() or None,
        variant_name=display,
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


def thaw_owned_prompt(job: Dict[str, Any], *, at: Optional[str] = None) -> bool:
    """Reopen a frozen prompt after a failed run so the job can be edited and retried."""
    owned = get_owned_prompt(job)
    if owned is None or not owned.get("frozen"):
        return False
    owned["frozen"] = False
    owned["thawed_at"] = at or utc_now_iso()
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
        # Remap legacy meta sidecar bindings without rewriting frozen text.
        src = str(existing.get("source_profile") or "").strip()
        if src and is_prompt_catalog_meta_file(src) and data_root is not None:
            family = str(job.get("family_slug") or "").strip()
            coerced = coerce_library_prompt_path(src, data_root=Path(data_root), family_slug=family)
            if coerced is not None and coerced.is_file():
                existing["source_profile"] = str(coerced.resolve())
                if not existing.get("variant_id"):
                    try:
                        doc = json.loads(coerced.read_text(encoding="utf-8"))
                        if isinstance(doc, dict) and doc.get("variant_id"):
                            existing["variant_id"] = doc["variant_id"]
                            display = human_variant_name(doc, coerced.stem)
                            existing["name"] = display
                            existing["variant_name"] = display
                    except Exception:
                        pass
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
    if data_root is not None and (not path.is_file() or is_prompt_catalog_meta_file(path)):
        family = str(job.get("family_slug") or "").strip()
        coerced = coerce_library_prompt_path(raw, data_root=Path(data_root), family_slug=family)
        if coerced is not None:
            path = coerced
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
    display_name = str(owned.get("name") or owned.get("variant_name") or "").strip()
    if not display_name and seed is not None:
        display_name = str(seed.get("name") or "").strip()
    if display_name and display_name.lower() not in _DEFAULTISH_NAMES:
        out["name"] = display_name
    elif seed is not None:
        out["name"] = human_variant_name(seed, basename)
    elif display_name:
        out["name"] = human_variant_name({"name": display_name}, basename)
    vid = str(owned.get("variant_id") or "").strip()
    if not vid and seed is not None:
        vid = str(seed.get("variant_id") or "").strip()
    if vid:
        out["variant_id"] = vid
    snap = str(owned.get("variant_name") or "").strip()
    if snap:
        out["variant_name"] = human_variant_name({"name": snap})
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
    else:
        seed["name"] = human_variant_name(doc, path.stem)
    vid = str(doc.get("variant_id") or "").strip()
    if vid:
        seed["variant_id"] = vid
    if "available" in doc:
        seed["available"] = bool(doc.get("available"))
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


PROMPT_CATALOG_INDEX_NAME = "prompt_catalog.json"
# Legacy location (inside prompts/) — must never be treated as a variant.
PROMPT_CATALOG_INDEX_LEGACY_NAME = "_index.json"
PROMPT_CATALOG_SCHEMA = "comfyui-runpod.prompt-catalog.v0"
_DEFAULTISH_NAMES = frozenset({"default", "catalog-default", "catalog default"})


def human_variant_name(doc: Optional[Dict[str, Any]] = None, *fallbacks: Any, default: str = "Base") -> str:
    """Operator-facing name; never returns bare 'default' / 'catalog-default'."""
    candidates: list[Any] = []
    if isinstance(doc, dict):
        candidates.extend(
            [
                doc.get("name"),
                doc.get("variant_name"),
                doc.get("label"),
                doc.get("slug"),
            ]
        )
    candidates.extend(fallbacks)
    for cand in candidates:
        text = str(cand or "").strip()
        if not text:
            continue
        # Strip path / extension noise
        text = text.replace("\\", "/").rsplit("/", 1)[-1]
        if text.lower().endswith(".json"):
            text = text[:-5]
        lower = text.lower()
        for prefix in _VARIANT_WRAPPERS:
            if lower.startswith(prefix):
                text = text[len(prefix) :]
                lower = text.lower()
                break
        if lower in _DEFAULTISH_NAMES or lower.replace("_", "-") in _DEFAULTISH_NAMES:
            continue
        if lower in {"index", "_index"} or text.startswith("_"):
            continue
        return text
    return default


def prompt_catalog_index_path(data_root: Path, family_slug: str) -> Path:
    """Family designation sidecar — sibling of ``prompts/``, not inside it."""
    return Path(data_root).expanduser().resolve() / "pools" / str(family_slug).strip() / PROMPT_CATALOG_INDEX_NAME


def prompt_catalog_index_legacy_path(data_root: Path, family_slug: str) -> Path:
    return family_prompts_dir(data_root, family_slug) / PROMPT_CATALOG_INDEX_LEGACY_NAME


def load_prompt_catalog_index(data_root: Path, family_slug: str) -> Dict[str, Any]:
    path = prompt_catalog_index_path(data_root, family_slug)
    legacy = prompt_catalog_index_legacy_path(data_root, family_slug)
    read_path = path if path.is_file() else legacy if legacy.is_file() else None
    if read_path is None:
        return {"schema_version": PROMPT_CATALOG_SCHEMA, "default_variant_id": None}
    try:
        raw = json.loads(read_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"schema_version": PROMPT_CATALOG_SCHEMA, "default_variant_id": None}
    if not isinstance(raw, dict):
        return {"schema_version": PROMPT_CATALOG_SCHEMA, "default_variant_id": None}
    out = {
        "schema_version": str(raw.get("schema_version") or PROMPT_CATALOG_SCHEMA),
        "default_variant_id": str(raw.get("default_variant_id") or "").strip() or None,
    }
    return out


def save_prompt_catalog_index(
    data_root: Path,
    family_slug: str,
    *,
    default_variant_id: Optional[str],
) -> Dict[str, Any]:
    path = prompt_catalog_index_path(data_root, family_slug)
    doc = {
        "schema_version": PROMPT_CATALOG_SCHEMA,
        "default_variant_id": str(default_variant_id or "").strip() or None,
    }
    try:
        _atomic_write_json(path, doc)
    except OSError as exc:
        return _catalog_write_error(exc, path)
    # Drop legacy prompts/_index.json so pickers never list "Index".
    legacy = prompt_catalog_index_legacy_path(data_root, family_slug)
    if legacy.is_file():
        try:
            legacy.unlink()
        except OSError:
            pass
    return {"ok": True, "path": str(path.resolve()), "index": doc}


def is_prompt_catalog_meta_file(path: Path | str) -> bool:
    """True for designation sidecars / underscore meta files — not prompt variants."""
    name = Path(path).name
    if name in {PROMPT_CATALOG_INDEX_NAME, PROMPT_CATALOG_INDEX_LEGACY_NAME}:
        return True
    return name.startswith("_")


def family_default_library_prompt_path(
    data_root: Path,
    family_slug: str,
) -> Optional[Path]:
    """Resolve the designated default library prompt file for a family."""
    family = str(family_slug or "").strip()
    if not family:
        return None
    index = load_prompt_catalog_index(data_root, family)
    vid = str(index.get("default_variant_id") or "").strip()
    if vid:
        found = find_library_prompt_by_variant_id(data_root, family, vid)
        if found is not None:
            return found[0]
    fallback = family_prompts_dir(data_root, family) / "catalog-default.json"
    return fallback if fallback.is_file() else None


def coerce_library_prompt_path(
    raw: str,
    *,
    data_root: Optional[Path] = None,
    family_slug: Optional[str] = None,
) -> Optional[Path]:
    """Resolve a library prompt path; remap catalog meta files to the family default."""
    text = str(raw or "").strip()
    if not text:
        return None

    def _family_from_path(p: Path | str) -> str:
        parts = str(p).replace("\\", "/").split("/")
        if "pools" in parts:
            i = parts.index("pools")
            if i + 1 < len(parts):
                return parts[i + 1]
        return ""

    family = str(family_slug or "").strip() or _family_from_path(text)
    # Meta even if the file was deleted (legacy prompts/_index.json).
    if is_prompt_catalog_meta_file(text):
        if data_root is None or not family:
            return None
        return family_default_library_prompt_path(Path(data_root), family)

    path = _resolve_prompt_profile_path(text, data_root=data_root)
    if path is None:
        return None
    if not is_prompt_catalog_meta_file(path):
        return path
    family = family or _family_from_path(path)
    if data_root is None or not family:
        return None
    return family_default_library_prompt_path(Path(data_root), family)


def iter_library_prompt_paths(data_root: Path, family_slug: str) -> list[Path]:
    prompts_dir = family_prompts_dir(data_root, family_slug)
    if not prompts_dir.is_dir():
        return []
    rows: list[Path] = []
    for path in sorted(prompts_dir.glob("*.json")):
        if not path.is_file() or is_prompt_catalog_meta_file(path):
            continue
        rows.append(path)
    return rows


def _read_library_doc(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return raw if isinstance(raw, dict) else None


def normalize_library_prompt_doc(
    doc: Dict[str, Any],
    *,
    path: Optional[Path] = None,
    mint_missing_id: bool = True,
) -> tuple[Dict[str, Any], bool]:
    """Ensure variant_id, name, available. Returns (doc, changed)."""
    changed = False
    out = dict(doc)
    vid = str(out.get("variant_id") or "").strip()
    if not vid and mint_missing_id:
        out["variant_id"] = str(uuid.uuid4())
        changed = True
    stem = path.stem if path is not None else ""
    name = human_variant_name(out, stem)
    if str(out.get("name") or "").strip() != name:
        out["name"] = name
        changed = True
    if "available" not in out:
        out["available"] = True
        changed = True
    elif not isinstance(out.get("available"), bool):
        out["available"] = bool(out.get("available"))
        changed = True
    if not out.get("content_hash"):
        out["content_hash"] = prompt_content_hash(out.get("positive"), out.get("negative"))
        changed = True
    # Slug stays stem/label-derived for job-key compatibility; name is display-only.
    variant_slug = prompt_variant_slug(out.get("slug"), out.get("label"), stem)
    if variant_slug and str(out.get("slug") or "") != variant_slug:
        out["slug"] = variant_slug
        changed = True
    return out, changed


def find_library_prompt_by_variant_id(
    data_root: Path,
    family_slug: str,
    variant_id: str,
) -> Optional[tuple[Path, Dict[str, Any]]]:
    want = str(variant_id or "").strip()
    if not want:
        return None
    for path in iter_library_prompt_paths(data_root, family_slug):
        doc = _read_library_doc(path)
        if not doc:
            continue
        if str(doc.get("variant_id") or "").strip() == want:
            return path, doc
    return None


def list_prompt_variants(
    data_root: Path,
    family_slug: str,
    *,
    include_unavailable: bool = True,
) -> list[Dict[str, Any]]:
    """Catalog rows with identity fields for APIs / pickers."""
    family = str(family_slug or "").strip()
    if not family:
        return []
    index = load_prompt_catalog_index(data_root, family)
    default_id = str(index.get("default_variant_id") or "").strip() or None
    rows: list[Dict[str, Any]] = []
    for path in iter_library_prompt_paths(data_root, family):
        doc = _read_library_doc(path) or {}
        norm, _ = normalize_library_prompt_doc(doc, path=path, mint_missing_id=False)
        available = bool(norm.get("available", True))
        if not include_unavailable and not available:
            continue
        vid = str(norm.get("variant_id") or "").strip() or None
        name = human_variant_name(norm, path.stem)
        # Prefer stem/label for slug stability (job-key / hourly heuristics); name is display-only.
        slug = (
            prompt_variant_slug(norm.get("slug"), norm.get("label"), path.stem, path.name, name)
            or path.stem
        )
        row: Dict[str, Any] = {
            "variant_id": vid,
            "name": name,
            "available": available,
            "is_default": bool(vid and default_id and vid == default_id),
            "slug": slug,
            "file_stem": path.stem,
            "label": str(norm.get("label") or path.stem),
            "basename": path.name,
            "path": str(path.resolve()),
            "content_hash": str(norm.get("content_hash") or ""),
        }
        rows.append(row)

    def _sort_key(row: Dict[str, Any]) -> tuple:
        if row.get("is_default"):
            return (0, str(row.get("name") or "").lower())
        if row.get("available"):
            return (1, str(row.get("name") or "").lower())
        return (2, str(row.get("name") or "").lower())

    rows.sort(key=_sort_key)
    return rows


def set_default_prompt_variant(
    data_root: Path,
    family_slug: str,
    variant_id: str,
) -> Dict[str, Any]:
    family = str(family_slug or "").strip()
    vid = str(variant_id or "").strip()
    if not family or not vid:
        return {"ok": False, "error": "missing_family_or_variant_id"}
    found = find_library_prompt_by_variant_id(data_root, family, vid)
    if found is None:
        return {"ok": False, "error": "variant_not_found", "variant_id": vid}
    path, doc = found
    if not bool(doc.get("available", True)):
        return {
            "ok": False,
            "error": "variant_unavailable",
            "detail": "Set available=true before designating as default.",
            "variant_id": vid,
        }
    saved = save_prompt_catalog_index(data_root, family, default_variant_id=vid)
    if not saved.get("ok"):
        return saved
    return {
        "ok": True,
        "family_slug": family,
        "default_variant_id": vid,
        "path": str(path.resolve()),
        "index_path": saved.get("path"),
    }


def rename_prompt_variant(
    data_root: Path,
    family_slug: str,
    variant_id: str,
    name: str,
) -> Dict[str, Any]:
    family = str(family_slug or "").strip()
    vid = str(variant_id or "").strip()
    new_name = str(name or "").strip()
    if not family or not vid:
        return {"ok": False, "error": "missing_family_or_variant_id"}
    if not new_name:
        return {"ok": False, "error": "missing_name"}
    if new_name.lower() in _DEFAULTISH_NAMES:
        return {
            "ok": False,
            "error": "invalid_name",
            "detail": '"default" is a designation, not a variant name.',
        }
    found = find_library_prompt_by_variant_id(data_root, family, vid)
    if found is None:
        return {"ok": False, "error": "variant_not_found", "variant_id": vid}
    path, doc = found
    doc = dict(doc)
    doc["name"] = new_name
    doc["slug"] = slugify_variant_label(new_name, fallback="variant", reserved=True)
    try:
        _atomic_write_json(path, doc)
    except OSError as exc:
        return _catalog_write_error(exc, path)
    return {"ok": True, "variant_id": vid, "name": new_name, "path": str(path.resolve()), "doc": doc}


def set_prompt_variant_available(
    data_root: Path,
    family_slug: str,
    variant_id: str,
    available: bool,
) -> Dict[str, Any]:
    family = str(family_slug or "").strip()
    vid = str(variant_id or "").strip()
    if not family or not vid:
        return {"ok": False, "error": "missing_family_or_variant_id"}
    found = find_library_prompt_by_variant_id(data_root, family, vid)
    if found is None:
        return {"ok": False, "error": "variant_not_found", "variant_id": vid}
    path, doc = found
    index = load_prompt_catalog_index(data_root, family)
    default_id = str(index.get("default_variant_id") or "").strip()
    if not available and default_id == vid:
        return {
            "ok": False,
            "error": "default_must_stay_available",
            "detail": "Reassign the family default before hiding this variant.",
            "variant_id": vid,
        }
    doc = dict(doc)
    doc["available"] = bool(available)
    try:
        _atomic_write_json(path, doc)
    except OSError as exc:
        return _catalog_write_error(exc, path)
    return {
        "ok": True,
        "variant_id": vid,
        "available": bool(available),
        "path": str(path.resolve()),
        "doc": doc,
    }


def backfill_prompt_catalog(
    data_root: Path,
    *,
    family_slug: Optional[str] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    """Mint variant_id / name / available and write pools/<family>/prompt_catalog.json."""
    root = Path(data_root).expanduser().resolve()
    pools = root / "pools"
    if not pools.is_dir():
        return {"ok": False, "error": "pools_missing", "path": str(pools)}
    families: list[str] = []
    want = str(family_slug or "").strip()
    if want:
        families = [want]
    else:
        for child in sorted(pools.iterdir()):
            if child.is_dir() and (child / "prompts").is_dir():
                families.append(child.name)
    report: Dict[str, Any] = {"ok": True, "apply": bool(apply), "families": []}
    for fam in families:
        fam_report: Dict[str, Any] = {"family_slug": fam, "variants": [], "writes": 0}
        index = load_prompt_catalog_index(root, fam)
        default_id = str(index.get("default_variant_id") or "").strip() or None
        former_default_path: Optional[Path] = None
        for path in iter_library_prompt_paths(root, fam):
            doc = _read_library_doc(path) or {}
            norm, changed = normalize_library_prompt_doc(doc, path=path, mint_missing_id=True)
            if path.name == "catalog-default.json":
                former_default_path = path
            fam_report["variants"].append(
                {
                    "path": str(path),
                    "variant_id": norm.get("variant_id"),
                    "name": norm.get("name"),
                    "available": norm.get("available"),
                    "changed": changed,
                }
            )
            if apply and changed:
                try:
                    _atomic_write_json(path, norm)
                    fam_report["writes"] += 1
                except OSError as exc:
                    fam_report["error"] = _catalog_write_error(exc, path)
                    report["ok"] = False
                    report["families"].append(fam_report)
                    return report
            elif not apply:
                # Use normalized id for designation preview even when dry-run.
                pass
            # Keep latest normalized id for designation when this was catalog-default.
            if path.name == "catalog-default.json":
                default_id = default_id or str(norm.get("variant_id") or "").strip() or None

        if not default_id and former_default_path is not None:
            # Re-read after potential write
            d = _read_library_doc(former_default_path) or {}
            if apply:
                d, _ = normalize_library_prompt_doc(d, path=former_default_path, mint_missing_id=True)
            default_id = str(d.get("variant_id") or "").strip() or None
        if not default_id and fam_report["variants"]:
            default_id = str(fam_report["variants"][0].get("variant_id") or "").strip() or None

        fam_report["default_variant_id"] = default_id
        if apply and default_id:
            saved = save_prompt_catalog_index(root, fam, default_variant_id=default_id)
            if not saved.get("ok"):
                fam_report["index_error"] = saved
                report["ok"] = False
            else:
                fam_report["index_path"] = saved.get("path")
        report["families"].append(fam_report)
    return report


def _atomic_write_json(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _bak_path(path: Path) -> Path:
    stamp = utc_now_iso().replace(":", "").replace("-", "")
    return path.with_name(f"{path.name}.bak.{stamp}")


def _catalog_write_error(exc: OSError, path: Path) -> Dict[str, Any]:
    if getattr(exc, "errno", None) == 30:
        return {
            "ok": False,
            "error": "catalog_read_only",
            "detail": (
                "Family catalog is mounted read-only in the container. "
                "docker-compose binds ./.data/pools and ./.data/shapes writable — "
                "recreate the container (`docker compose up -d`) after that change."
            ),
            "path": str(path),
        }
    return {"ok": False, "error": "catalog_write_failed", "detail": str(exc), "path": str(path)}


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
    available: bool = True,
) -> Dict[str, Any]:
    vid = str(variant_id or "").strip() or str(uuid.uuid4())
    display_name = human_variant_name(
        {"name": name, "label": label},
        name,
        label,
    )
    doc: Dict[str, Any] = {
        "label": label,
        "positive": str(positive or ""),
        "negative": str(negative or ""),
        "variant_id": vid,
        "name": display_name,
        "available": bool(available),
        "content_hash": prompt_content_hash(positive, negative),
        "created_at": utc_now_iso(),
    }
    variant_slug = prompt_variant_slug(doc.get("slug"), display_name, label)
    if variant_slug in _RESERVED_VARIANT_SLUGS:
        variant_slug = slugify_variant_label(display_name, fallback="variant", reserved=True)
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
    name: Optional[str] = None,
    note: Optional[str] = None,
    promoted_from_job: Optional[str] = None,
    parent_path: Optional[str] = None,
    variant_id: Optional[str] = None,
    set_as_default: bool = False,
) -> Dict[str, Any]:
    """
    Write a prompt profile into ``pools/<family>/prompts/``.

    ``mode=fork`` (default): new file + new ``variant_id``.
    ``mode=update``: write text into existing ``variant_id`` (preserves id + name).
    ``mode=overwrite``: legacy alias — update the designated default (or
    ``catalog-default.json``), preserving that entry's ``variant_id`` when present.
    """
    family = str(family_slug or "").strip()
    if not family:
        return {"ok": False, "error": "missing_family"}
    prompts_dir = family_prompts_dir(data_root, family)
    mode_s = str(mode or "fork").strip().lower()
    if mode_s not in {"fork", "overwrite", "update"}:
        return {"ok": False, "error": "bad_mode", "detail": "mode must be fork|update|overwrite"}

    parent_variant_id = None
    parent = str(parent_path or "").strip() or None
    if parent:
        try:
            pdoc = json.loads(Path(parent).expanduser().read_text(encoding="utf-8"))
            if isinstance(pdoc, dict) and pdoc.get("variant_id"):
                parent_variant_id = str(pdoc.get("variant_id"))
        except Exception:
            pass

    if mode_s in {"overwrite", "update"}:
        target: Optional[Path] = None
        existing: Optional[Dict[str, Any]] = None
        keep_vid = str(variant_id or "").strip() or None
        if mode_s == "update" or keep_vid:
            if not keep_vid:
                return {"ok": False, "error": "missing_variant_id", "detail": "update requires variant_id"}
            found = find_library_prompt_by_variant_id(data_root, family, keep_vid)
            if found is None:
                return {"ok": False, "error": "variant_not_found", "variant_id": keep_vid}
            target, existing = found
        else:
            # overwrite → designated default, else catalog-default.json
            index = load_prompt_catalog_index(data_root, family)
            def_id = str(index.get("default_variant_id") or "").strip()
            if def_id:
                found = find_library_prompt_by_variant_id(data_root, family, def_id)
                if found is not None:
                    target, existing = found
            if target is None:
                target = prompts_dir / "catalog-default.json"
                existing = _read_library_doc(target) if target.is_file() else None

        assert target is not None
        keep_vid = str((existing or {}).get("variant_id") or keep_vid or "").strip() or str(uuid.uuid4())
        keep_name = human_variant_name(existing, name, label, target.stem)
        label_s = str(label or (existing or {}).get("label") or target.stem).strip() or target.stem
        bak = None
        doc = build_library_prompt_doc(
            positive=positive,
            negative=negative,
            label=label_s,
            name=keep_name if not name else human_variant_name({"name": name}),
            parent_path=parent or (str(target.resolve()) if target.is_file() else None),
            parent_variant_id=parent_variant_id,
            promoted_from_job=promoted_from_job,
            note=note,
            variant_id=keep_vid,
            available=bool((existing or {}).get("available", True)),
        )
        # Preserve created_at / name when updating in place unless rename requested.
        if existing and existing.get("created_at"):
            doc["created_at"] = existing["created_at"]
            doc["updated_at"] = utc_now_iso()
        if existing and not name:
            doc["name"] = keep_name
            doc["slug"] = prompt_variant_slug(existing.get("slug"), keep_name) or doc.get("slug")
        try:
            if target.is_file():
                bak = _bak_path(target)
                bak.write_bytes(target.read_bytes())
            _atomic_write_json(target, doc)
        except OSError as exc:
            return _catalog_write_error(exc, bak or target)
        out: Dict[str, Any] = {
            "ok": True,
            "mode": "update" if mode_s == "update" else "overwrite",
            "path": str(target.resolve()),
            "bak_path": str(bak.resolve()) if bak else None,
            "doc": doc,
            "variant_id": doc["variant_id"],
        }
        if set_as_default:
            des = set_default_prompt_variant(data_root, family, doc["variant_id"])
            out["set_as_default"] = des
        return out

    display = human_variant_name({"name": name, "label": label}, name, label)
    label_s = str(label or display).strip() or f"variant-{utc_now_iso()[:10]}"
    slug = slugify_variant_label(display or label_s, fallback="variant")
    target = prompts_dir / f"{slug}.json"
    n = 2
    while target.is_file():
        target = prompts_dir / f"{slug}-{n}.json"
        n += 1
    doc = build_library_prompt_doc(
        positive=positive,
        negative=negative,
        label=label_s,
        name=display,
        parent_path=parent,
        parent_variant_id=parent_variant_id,
        promoted_from_job=promoted_from_job,
        note=note,
        variant_id=str(variant_id or "").strip() or None,
    )
    try:
        _atomic_write_json(target, doc)
    except OSError as exc:
        return _catalog_write_error(exc, target)
    out = {
        "ok": True,
        "mode": "fork",
        "path": str(target.resolve()),
        "bak_path": None,
        "doc": doc,
        "variant_id": doc["variant_id"],
    }
    if set_as_default:
        des = set_default_prompt_variant(data_root, family, doc["variant_id"])
        out["set_as_default"] = des
    return out


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
