"""Job-owned prompt fork (V1): catalog is a seed; the job holds runtime truth.

See plan: Prompt request/order design — V1 treats each ``.job.json`` as an order
with inline ``job["prompt"]``. Macros / request-vs-order split come later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


class OwnedPromptFrozenError(RuntimeError):
    """Raised when mutating a prompt after execution start (cabin doors)."""


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fork_owned_prompt(
    *,
    positive: Any = "",
    negative: Any = "",
    label: Any = None,
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
    if source_profile:
        out["source_profile"] = str(source_profile)
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
        source_profile=source_profile,
        frozen=False,
    )


def fork_owned_prompt_from_profile_file(path: Path) -> Dict[str, Any]:
    p = Path(path).expanduser()
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"prompt profile is not a JSON object: {p}")
    return fork_owned_prompt_from_profile_doc(doc, source_profile=str(p.resolve()))


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
) -> Dict[str, Any]:
    """Shape compatible with ``_prompt_excerpt`` / Workbench PromptPeek."""
    from shape_factory_work_products import decode_prompt_markup

    positive = str(owned.get("positive") or "")
    negative = str(owned.get("negative") or "")
    source = str(owned.get("source_profile") or "").strip()
    basename = Path(source).name if source else str(owned.get("label") or "owned-prompt")
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
    }
    if owned.get("frozen_at"):
        out["frozen_at"] = owned.get("frozen_at")
    if positive:
        out["positive_excerpt"] = positive if len(positive) <= max_chars else positive[: max_chars - 1] + "…"
        out["positive_chars"] = len(positive)
    if negative:
        out["negative_excerpt"] = negative if len(negative) <= 120 else negative[:119] + "…"
        out["negative_chars"] = len(negative)
    return out
