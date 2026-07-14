#!/usr/bin/env python3
"""Build and query inferred ratings index from og/ XMP stars + shape_factory jobs."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from correlate_output_ratings import extract_workflow_png, iter_rated_og_records, normalize_source_basename
from snowflake_inventory import graph_fingerprint, is_litegraph_workflow

RATINGS_SCHEMA_VERSION = 1
APPETITE_SCHEMA_VERSION = 1

# Appetite ("do more WITH this") is a second, direction axis distinct from the
# quality star ("do more OF this"). Ordinal, strongest first.
APPETITE_STATES: Tuple[str, ...] = ("less", "neutral", "more", "fast_track")
APPETITE_FACETS: Tuple[str, ...] = ("both", "source", "processing")

# Quality is three explicit sub-axes; ``explicit`` is their rounded mean for XMP/legacy.
QUALITY_AXES: Tuple[str, ...] = ("subject_beauty", "render_quality", "action_quality")
QUALITY_AXIS_ALIASES: Dict[str, str] = {
    "subject": "subject_beauty",
    "beauty": "subject_beauty",
    "subject_beauty": "subject_beauty",
    "render": "render_quality",
    "render_quality": "render_quality",
    "action": "action_quality",
    "action_quality": "action_quality",
}

# Valid keeper stars are 1–5. ``explicit: 0`` (or negative) means omit from
# selection / heuristics — not "unrated" (unrated is ``explicit is None``).
QUALITY_RATING_MIN = 1
QUALITY_RATING_MAX = 5


def is_usable_quality_rating(value: Any) -> bool:
    """True when ``value`` is a real 1–5 quality score."""
    if value is None:
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    if numeric != numeric:  # NaN
        return False
    return QUALITY_RATING_MIN <= numeric <= QUALITY_RATING_MAX


def is_omit_quality_rating(value: Any) -> bool:
    """
    True when an explicit rating means "leave this out of consideration".

    Distinguishes from unrated (``None`` / missing): omit is an intentional
    non-keeper mark stored as ``explicit: 0`` (legacy) or any non-positive value.
    """
    if value is None:
        return False
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    if numeric != numeric:
        return False
    return numeric <= 0

# Numeric value used when appetite is rolled up (mirrors 1-5 rating scale) and the
# derive-selection multiplier appetite applies on top of a candidate's base weight.
APPETITE_SCORE: Dict[str, float] = {
    "less": 1.0,
    "neutral": 2.5,
    "more": 4.0,
    "fast_track": 5.0,
}
APPETITE_MULT: Dict[str, float] = {
    "less": 0.1,
    "neutral": 1.0,
    "more": 2.5,
    "fast_track": 6.0,
}


def normalize_quality_axis(value: Any) -> str:
    """Return a canonical quality axis id or '' if unknown."""
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return QUALITY_AXIS_ALIASES.get(raw, "")


def normalize_axes_map(raw: Any) -> Dict[str, int]:
    """Return only valid 1–5 axis values from a row's ``axes`` object."""
    out: Dict[str, int] = {}
    if not isinstance(raw, dict):
        return out
    for axis in QUALITY_AXES:
        val = raw.get(axis)
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 5:
            out[axis] = n
    return out


def axes_complete(axes: Dict[str, int]) -> bool:
    """True when all three quality axes are set to 1–5."""
    return all(axis in axes and 1 <= int(axes[axis]) <= 5 for axis in QUALITY_AXES)


def aggregate_explicit_from_axes(axes: Dict[str, int]) -> Optional[int]:
    """Rounded mean of set axes (1–5), or None when empty."""
    vals = [int(axes[a]) for a in QUALITY_AXES if a in axes]
    if not vals:
        return None
    return int(round(statistics.mean(vals)))


def normalize_appetite(value: Any) -> str:
    """Return a canonical appetite state or '' (clear/unset)."""
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in ("", "none", "clear", "unset"):
        return ""
    if raw in ("fasttrack", "fast"):
        raw = "fast_track"
    return raw if raw in APPETITE_STATES else ""


def normalize_appetite_facet(value: Any) -> str:
    """Return a canonical appetite facet, defaulting to 'both'."""
    raw = str(value or "").strip().lower()
    if raw in ("look", "recipe", "process"):
        raw = "processing"
    return raw if raw in APPETITE_FACETS else "both"


@dataclass
class AggBucket:
    ratings: List[int] = field(default_factory=list)

    def add(self, rating: int) -> None:
        self.ratings.append(int(rating))

    def to_inferred(self, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.ratings:
            return {}
        keepers = sum(1 for r in self.ratings if r >= 4)
        out: Dict[str, Any] = {
            "inferred": round(statistics.mean(self.ratings), 2),
            "n": len(self.ratings),
            "keepers_4plus": keepers,
        }
        if extra:
            out.update(extra)
        return out


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_path_key(path_like: str, data_root: Path) -> List[str]:
    """Return lookup keys for a filesystem or relpath string."""
    raw = str(path_like or "").strip().replace("\\", "/")
    if not raw:
        return []
    keys: List[str] = []
    seen: set[str] = set()

    def add(k: str) -> None:
        k = k.strip().replace("\\", "/")
        if not k or k in seen:
            return
        seen.add(k)
        keys.append(k)

    add(raw)
    p = Path(raw).expanduser()
    if p.is_absolute():
        try:
            add(str(p.resolve().relative_to(data_root.resolve())))
        except ValueError:
            pass
        add(str(p.resolve()))
    else:
        add(str((data_root / raw).resolve()))

    for k in list(keys):
        add(k.replace("/output/output/", "/output/"))
        add(re.sub(r"^.*?/output/output/", "output/", k))
        if k.endswith(".mp4"):
            add(k[:-4])
        base = Path(k).name
        if base:
            add(base)
            if base.endswith(".mp4"):
                add(base[:-4])
    return keys


def _prompt_profile_name(job: Dict[str, Any]) -> Optional[str]:
    bindings = job.get("bindings")
    if not isinstance(bindings, dict):
        return None
    pp = bindings.get("prompt_profile")
    if not isinstance(pp, dict):
        return None
    path = str(pp.get("path") or "")
    if not path:
        return None
    stem = Path(path).stem
    return stem or None


def _catalog_slug(job: Dict[str, Any]) -> Optional[str]:
    family = str(job.get("family_slug") or "").strip()
    if family:
        return family
    template = str(job.get("template_path") or "")
    if template:
        name = Path(template).stem
        if name.endswith("-readable"):
            return name[: -len("-readable")]
        return name
    return None


def _shape_recipe_key(job: Dict[str, Any]) -> Optional[str]:
    family = str(job.get("family_slug") or _catalog_slug(job) or "").strip()
    profile = _prompt_profile_name(job)
    if not family or not profile:
        return None
    return f"{family}+{profile}"


def _job_meta_from(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "graph_hash": job.get("graph_hash"),
        "shape_id": job.get("shape_id"),
        "family_slug": job.get("family_slug"),
        "catalog_slug": _catalog_slug(job),
        "shape_recipe": _shape_recipe_key(job),
        "job_key": job.get("job_key"),
    }


def build_job_output_index(jobs_root: Path, data_root: Path) -> Dict[str, Dict[str, Any]]:
    """Map normalized output paths to shape_factory job metadata."""
    index: Dict[str, Dict[str, Any]] = {}
    if not jobs_root.is_dir():
        return index

    for job_path in sorted(jobs_root.rglob("*.job.json")):
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(job, dict):
            continue
        meta = _job_meta_from(job)
        paths: List[str] = []

        submit = job.get("submit")
        if isinstance(submit, dict):
            outputs = submit.get("outputs")
            if isinstance(outputs, list):
                paths.extend(str(p) for p in outputs if p)

        deposit = job.get("deposit")
        if isinstance(deposit, dict):
            videos = deposit.get("videos")
            if isinstance(videos, list):
                paths.extend(str(p) for p in videos if p)

        prefix = str(job.get("output_prefix") or "").strip()
        if prefix:
            paths.append(prefix)

        for raw in paths:
            for key in _norm_path_key(raw, data_root):
                index.setdefault(key, meta)
    return index


def _graph_hash_for_record(rec: Dict[str, Any], job_index: Dict[str, Dict[str, Any]], data_root: Path) -> Tuple[Optional[str], Optional[str], bool]:
    """Return (graph_hash, shape_recipe, from_job)."""
    lookup_keys: List[str] = []
    for key_name in ("output_discovery_key", "output_short_key"):
        lookup_keys.extend(_norm_path_key(str(rec.get(key_name) or ""), data_root))
    xmp_path = Path(str(rec.get("xmp_path") or ""))
    if xmp_path.is_file():
        for ext in (".mp4", ".png"):
            lookup_keys.extend(_norm_path_key(str(xmp_path.with_suffix(ext)), data_root))

    meta = _lookup_job_meta(lookup_keys, job_index)
    if meta and meta.get("graph_hash"):
        return str(meta["graph_hash"]), meta.get("shape_recipe"), True

    if xmp_path.is_file():
        wf = extract_workflow_png(xmp_path.with_suffix(".png"))
        if wf and is_litegraph_workflow(wf):
            gh = graph_fingerprint(wf)
            if gh:
                return gh, None, False
    return None, None, False


def _lookup_job_meta(path_keys: Iterable[str], job_index: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for key in path_keys:
        meta = job_index.get(key)
        if meta:
            return meta
    return None


def build_ratings_index(
    *,
    og_root: Path,
    jobs_root: Path,
    data_root: Path,
    out_path: Path,
    name_glob: str = "*.XMP",
    days: int = 0,
    ffprobe: Optional[str] = None,
) -> Dict[str, Any]:
    og_root = og_root.expanduser().resolve()
    jobs_root = jobs_root.expanduser().resolve()
    data_root = data_root.expanduser().resolve()
    out_path = out_path.expanduser().resolve()

    job_index = build_job_output_index(jobs_root, data_root)
    records = iter_rated_og_records(
        og_root,
        name_glob=name_glob,
        days=days,
        ffprobe=ffprobe,
    )

    by_graph_hash: Dict[str, AggBucket] = defaultdict(AggBucket)
    graph_meta: Dict[str, Dict[str, Any]] = {}
    by_shape_recipe: Dict[str, AggBucket] = defaultdict(AggBucket)
    by_source_basename: Dict[str, AggBucket] = defaultdict(AggBucket)
    by_output_relpath: Dict[str, Dict[str, Any]] = {}
    source_contributors: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    graph_contributors: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    _MAX_CONTRIBUTORS = 48

    # Preserve operator-stamped fields across XMP rebuilds (rated_at, axes).
    prior_rows: Dict[str, Dict[str, Any]] = {}
    if out_path.is_file():
        try:
            prior_doc = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            prior_doc = {}
        prior_table = prior_doc.get("by_output_relpath") if isinstance(prior_doc, dict) else None
        if isinstance(prior_table, dict):
            for key, row in prior_table.items():
                if isinstance(row, dict):
                    prior_rows[str(key)] = row

    joined_jobs = 0
    joined_workflow = 0
    with_prompt = 0
    with_sources = 0

    for rec in records:
        rating = int(rec["rating"])
        discovery_key = str(rec["output_discovery_key"])
        short_key = str(rec["output_short_key"])
        usable = is_usable_quality_rating(rating)

        sources = rec.get("source_basenames") or []
        source_paths = rec.get("sources") or []
        output_row: Dict[str, Any] = {
            "explicit": rating,
            "short_key": short_key,
            "xmp": rec.get("xmp_path"),
            "sources": sources,
            "source_paths": source_paths,
        }

        if rec.get("prompt"):
            with_prompt += 1
        if sources:
            with_sources += 1
            if usable:
                for bn, raw in zip(sources, source_paths):
                    by_source_basename[bn].add(rating)
                    arr = source_contributors[bn]
                    if len(arr) < _MAX_CONTRIBUTORS:
                        arr.append(
                            {
                                "output_discovery_key": discovery_key,
                                "rating": rating,
                                "via_source": raw,
                            }
                        )

        gh, recipe, from_job = _graph_hash_for_record(rec, job_index, data_root)
        if gh:
            output_row["graph_hash"] = gh
            if from_job:
                joined_jobs += 1
            else:
                joined_workflow += 1
            if usable:
                by_graph_hash[gh].add(rating)
                garr = graph_contributors[gh]
                if len(garr) < _MAX_CONTRIBUTORS:
                    garr.append({"output_discovery_key": discovery_key, "rating": rating})
            if from_job:
                meta = _lookup_job_meta(_norm_path_key(discovery_key, data_root), job_index) or {}
                graph_meta.setdefault(
                    gh,
                    {
                        "catalog_slug": meta.get("catalog_slug"),
                        "shape_id": meta.get("shape_id"),
                    },
                )
            if recipe:
                output_row["shape_recipe"] = recipe
                if usable:
                    by_shape_recipe[str(recipe)].add(rating)

        prior = prior_rows.get(discovery_key) or prior_rows.get(short_key) or {}
        if isinstance(prior, dict):
            if prior.get("rated_at"):
                output_row["rated_at"] = prior.get("rated_at")
            prior_axes = normalize_axes_map(prior.get("axes"))
            if prior_axes:
                output_row["axes"] = {a: prior_axes[a] for a in QUALITY_AXES if a in prior_axes}
                derived = aggregate_explicit_from_axes(prior_axes)
                if derived is not None:
                    # Prefer axis aggregate over raw XMP when Discovery has set axes.
                    output_row["explicit"] = int(derived)

        by_output_relpath[discovery_key] = output_row
        by_output_relpath[short_key] = output_row

    doc: Dict[str, Any] = {
        "version": RATINGS_SCHEMA_VERSION,
        "updated_at": utc_now(),
        "stats": {
            "rated_outputs": len(records),
            "with_prompt": with_prompt,
            "with_sources": with_sources,
            "joined_shape_factory_jobs": joined_jobs,
            "joined_png_workflow_graph": joined_workflow,
            "job_output_keys": len(job_index),
        },
        "by_graph_hash": {},
        "by_shape_recipe": {},
        "by_source_basename": {},
        "by_output_relpath": {},
    }

    for gh, bucket in sorted(by_graph_hash.items(), key=lambda kv: (-statistics.mean(kv[1].ratings), -len(kv[1].ratings))):
        extra = graph_meta.get(gh) or {}
        row = bucket.to_inferred(extra=extra)
        row["contributors"] = graph_contributors.get(gh, [])
        doc["by_graph_hash"][gh] = row

    for key, bucket in sorted(by_shape_recipe.items(), key=lambda kv: (-statistics.mean(kv[1].ratings), -len(kv[1].ratings))):
        doc["by_shape_recipe"][key] = bucket.to_inferred()

    for bn, bucket in sorted(by_source_basename.items(), key=lambda kv: (-statistics.mean(kv[1].ratings), -len(kv[1].ratings))):
        row = bucket.to_inferred()
        row["favorite_fanout"] = row.get("keepers_4plus", 0)
        row["contributors"] = source_contributors.get(bn, [])
        doc["by_source_basename"][bn] = row

    doc["by_output_relpath"] = by_output_relpath

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc


def _load_index(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _prefix_match_keys(table: Dict[str, Any], needle: str) -> List[str]:
    n = needle.strip().lower()
    if not n:
        return []
    return [k for k in table if k.lower().startswith(n) or n in k.lower()]


def _output_lookup_keys(relpath: str) -> List[str]:
    norm = relpath.strip().replace("\\", "/").lstrip("/")
    keys: List[str] = []
    seen: set = set()

    def add(k: str) -> None:
        if k and k not in seen:
            seen.add(k)
            keys.append(k)

    add(norm)
    if norm.endswith(".mp4"):
        add(norm[:-4])
    if norm.endswith(".png"):
        add(norm[:-4])
    if not norm.startswith("output/") and norm.startswith("og/"):
        add(f"output/{norm}")
    return keys


def lookup_output_rating(output_path: str, ratings_doc: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Resolve explicit output rating row from ratings_index by path variants."""
    table = ratings_doc.get("by_output_relpath") or {}
    if not isinstance(table, dict):
        return None
    raw = str(output_path or "").strip().replace("\\", "/")
    if not raw:
        return None
    keys = [raw, Path(raw).name]
    if "/output/output/" in raw:
        keys.append(re.sub(r"^.*?/output/output/", "output/", raw))
    if "/og/" in raw:
        tail = raw.split("/og/", 1)[-1]
        keys.append(f"output/og/{tail.rstrip('/')}")
        keys.append(f"og/{tail.rstrip('/')}")
    expanded: List[str] = []
    for key in keys:
        key = key.strip().replace("\\", "/")
        if not key:
            continue
        expanded.append(key)
        for suffix in (".mp4", ".MP4", ".png", ".PNG", ".webm", ".WEBM"):
            if key.endswith(suffix):
                expanded.append(key[: -len(suffix)])
    seen: set[str] = set()
    for key in expanded:
        if not key or key in seen:
            continue
        seen.add(key)
        row = table.get(key)
        if isinstance(row, dict):
            return row
    return None


_XMP_RATING_ATTR_RE = re.compile(r'xmp:Rating="\d+"')
_XMP_RATING_TEMPLATE = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="{rating}"/>\n'
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json_doc(path: Path, obj: Any) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _find_existing_xmp(media_abs: Path) -> Optional[Path]:
    for ext in (".XMP", ".xmp"):
        cand = media_abs.with_suffix(ext)
        if cand.is_file():
            return cand
    return None


def _write_xmp_rating(media_abs: Path, stars: int) -> Path:
    existing = _find_existing_xmp(media_abs)
    target = existing or media_abs.with_suffix(".XMP")
    if existing is not None:
        txt = existing.read_text(encoding="utf-8", errors="replace")
        if _XMP_RATING_ATTR_RE.search(txt):
            txt = _XMP_RATING_ATTR_RE.sub(f'xmp:Rating="{stars}"', txt, count=1)
        else:
            m = re.search(r"<rdf:Description\b", txt)
            if m:
                if "xmlns:xmp=" not in txt:
                    txt = (
                        txt[: m.end()]
                        + ' xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
                        + f' xmp:Rating="{stars}"'
                        + txt[m.end() :]
                    )
                else:
                    txt = txt[: m.end()] + f' xmp:Rating="{stars}"' + txt[m.end() :]
            else:
                txt = _XMP_RATING_TEMPLATE.format(rating=stars)
    else:
        txt = _XMP_RATING_TEMPLATE.format(rating=stars)
    _atomic_write_text(target, txt)
    return target


def _clear_xmp_rating(media_abs: Path) -> Optional[Path]:
    existing = _find_existing_xmp(media_abs)
    if existing is None:
        return None
    txt = existing.read_text(encoding="utf-8", errors="replace")
    if _XMP_RATING_ATTR_RE.search(txt):
        # Drop the attribute (with any leading space) so the artifact reads as unrated.
        txt = re.sub(r'\s*xmp:Rating="\d+"', "", txt, count=1)
        _atomic_write_text(existing, txt)
    return existing


def _init_ratings_doc() -> Dict[str, Any]:
    return {
        "version": RATINGS_SCHEMA_VERSION,
        "updated_at": utc_now(),
        "stats": {},
        "by_graph_hash": {},
        "by_shape_recipe": {},
        "by_source_basename": {},
        "by_output_relpath": {},
    }


def _load_or_init_ratings_doc(ratings_index_path: Path) -> Dict[str, Any]:
    if ratings_index_path.is_file():
        try:
            doc = json.loads(ratings_index_path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                doc.setdefault("by_output_relpath", {})
                return doc
        except (OSError, json.JSONDecodeError):
            pass
    return _init_ratings_doc()


def _lookup_output_row_keys(
    media_abs: Path,
    media_relpath: str,
    og_root: Path,
) -> Tuple[str, str]:
    from correlate_output_ratings import output_relpath_keys_from_xmp

    xmp_like = media_abs.with_suffix(".XMP")
    try:
        short_key, discovery_key = output_relpath_keys_from_xmp(xmp_like, og_root)
    except ValueError:
        short_key = ""
        discovery_key = str(media_relpath or "").replace("\\", "/")
    return short_key, discovery_key


def _enrich_row_sources(
    media_abs: Path,
    row: Dict[str, Any],
    *,
    ffprobe: Optional[str] = None,
) -> None:
    from correlate_output_ratings import extract_prompt_media, extract_source_paths_from_prompt

    if row.get("sources") and row.get("source_paths"):
        return
    stem_dir = media_abs.parent
    stem_name = media_abs.stem
    prompt, _label = extract_prompt_media(stem_dir, stem_name, ffprobe=ffprobe)
    sources = extract_source_paths_from_prompt(prompt) if prompt else []
    row["sources"] = [normalize_source_basename(s) for s in sources]
    row["source_paths"] = sources


def set_output_quality_axis(
    *,
    media_abs: Path,
    media_relpath: str,
    axis: str,
    stars: int,
    og_root: Path,
    ratings_index_path: Path,
    ffprobe: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Set or clear one quality axis and refresh the derived ``explicit`` aggregate + XMP.

    Stars 1–5 set the axis; 0 clears it. ``explicit`` is the rounded mean of set axes.
    XMP ``xmp:Rating`` mirrors ``explicit`` (cleared when no axes remain).
    """
    axis_id = normalize_quality_axis(axis)
    if not axis_id:
        raise ValueError(f"bad axis: {axis!r} (expected one of {QUALITY_AXES})")
    stars = int(stars)
    if stars < 0 or stars > 5:
        raise ValueError("stars must be 0-5")
    media_abs = Path(media_abs)
    if not media_abs.is_file():
        raise FileNotFoundError(str(media_abs))
    og_root = Path(og_root).resolve()

    short_key, discovery_key = _lookup_output_row_keys(media_abs, media_relpath, og_root)
    doc = _load_or_init_ratings_doc(ratings_index_path)
    table = doc.setdefault("by_output_relpath", {})

    prev: Dict[str, Any] = {}
    for k in (discovery_key, short_key):
        if k and isinstance(table.get(k), dict):
            prev = dict(table[k])
            break

    axes = normalize_axes_map(prev.get("axes"))
    if stars <= 0:
        axes.pop(axis_id, None)
    else:
        axes[axis_id] = stars

    explicit = aggregate_explicit_from_axes(axes)
    xmp_like = media_abs.with_suffix(".XMP")
    if explicit is None:
        xmp_target = _clear_xmp_rating(media_abs)
        # Drop the output row when nothing remains (no axes, no legacy explicit).
        for k in (discovery_key, short_key):
            if k:
                table.pop(k, None)
        doc["updated_at"] = utc_now()
        _atomic_write_json_doc(ratings_index_path, doc)
        return {
            "ok": True,
            "relpath": media_relpath,
            "axis": axis_id,
            "stars": 0,
            "axes": {},
            "explicit": None,
            "cleared": True,
            "xmp_path": str(xmp_target) if xmp_target else None,
            "discovery_key": discovery_key,
            "short_key": short_key,
            "sources": prev.get("sources") or [],
        }

    xmp_target = _write_xmp_rating(media_abs, int(explicit))
    now = utc_now()
    row: Dict[str, Any] = {
        "explicit": int(explicit),
        "axes": {a: axes[a] for a in QUALITY_AXES if a in axes},
        "short_key": short_key or prev.get("short_key") or "",
        "xmp": str(xmp_target or xmp_like),
        "sources": list(prev.get("sources") or []),
        "source_paths": list(prev.get("source_paths") or []),
        # Wall-clock when an operator last set quality — used by hourly top-of-hour bias.
        "rated_at": now,
    }
    for keep in ("graph_hash", "shape_recipe"):
        if prev.get(keep):
            row[keep] = prev[keep]
    _enrich_row_sources(media_abs, row, ffprobe=ffprobe)
    for k in (discovery_key, short_key):
        if k:
            table[k] = row

    doc["updated_at"] = utc_now()
    _atomic_write_json_doc(ratings_index_path, doc)
    return {
        "ok": True,
        "relpath": media_relpath,
        "axis": axis_id,
        "stars": stars,
        "axes": row["axes"],
        "explicit": int(explicit),
        "cleared": False,
        "xmp_path": str(xmp_target) if xmp_target else None,
        "discovery_key": discovery_key,
        "short_key": short_key,
        "sources": row.get("sources") or [],
    }


def set_output_rating(
    *,
    media_abs: Path,
    media_relpath: str,
    stars: int,
    og_root: Path,
    ratings_index_path: Path,
    ffprobe: Optional[str] = None,
    axis: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Set quality rating(s) for one output.

    When ``axis`` is provided, updates that axis only. When omitted, sets all three
    axes to the same star value (compat / bulk). Stars 0 clears (one axis or all).
    """
    stars = int(stars)
    if stars < 0 or stars > 5:
        raise ValueError("stars must be 0-5")
    if axis:
        return set_output_quality_axis(
            media_abs=media_abs,
            media_relpath=media_relpath,
            axis=axis,
            stars=stars,
            og_root=og_root,
            ratings_index_path=ratings_index_path,
            ffprobe=ffprobe,
        )

    last: Dict[str, Any] = {"ok": True, "relpath": media_relpath, "stars": stars, "cleared": stars <= 0}
    for axis_id in QUALITY_AXES:
        last = set_output_quality_axis(
            media_abs=media_abs,
            media_relpath=media_relpath,
            axis=axis_id,
            stars=stars,
            og_root=og_root,
            ratings_index_path=ratings_index_path,
            ffprobe=ffprobe,
        )
    last["axis"] = None
    last["stars"] = stars
    return last


def verify_xmp_explicit(output_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    from correlate_output_ratings import parse_xmp_rating

    out: Dict[str, Any] = {"ok": False}
    if not isinstance(output_row, dict):
        out["error"] = "no_output_row"
        return out
    xmp_raw = output_row.get("xmp")
    index_explicit = output_row.get("explicit")
    out["index_explicit"] = index_explicit
    if not isinstance(xmp_raw, str) or not xmp_raw.strip():
        out["error"] = "no_xmp_path"
        return out
    xmp_path = Path(xmp_raw).expanduser()
    out["xmp_path"] = str(xmp_path)
    if not xmp_path.is_file():
        out["error"] = "xmp_missing"
        out["match"] = False
        return out
    try:
        out["xmp_mtime_iso"] = _dt.datetime.fromtimestamp(xmp_path.stat().st_mtime, tz=_dt.timezone.utc).isoformat(
            timespec="seconds"
        )
    except OSError:
        pass
    disk = parse_xmp_rating(xmp_path)
    out["xmp_on_disk"] = disk
    out["match"] = disk == index_explicit
    out["ok"] = True
    return out


def build_asset_ratings_explorer(
    *,
    relpath: str,
    ratings_doc: Dict[str, Any],
    item: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Per-asset ratings breakdown for Discovery explorer (explicit, inferred, evidence, verify)."""
    keys = _output_lookup_keys(relpath)
    by_output = ratings_doc.get("by_output_relpath") or {}
    by_source = ratings_doc.get("by_source_basename") or {}
    by_graph = ratings_doc.get("by_graph_hash") or {}
    by_recipe = ratings_doc.get("by_shape_recipe") or {}

    output_row: Optional[Dict[str, Any]] = None
    for k in keys:
        row = by_output.get(k)
        if isinstance(row, dict):
            output_row = row
            break

    basename = ""
    if isinstance(item, dict):
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            basename = Path(name.strip()).name
    if not basename:
        for k in keys:
            bn = Path(k).name
            if bn:
                basename = bn
                break

    explicit_block: Dict[str, Any] = {}
    axes_map: Dict[str, int] = {}
    if output_row:
        axes_map = normalize_axes_map(output_row.get("axes"))
        explicit_block = {
            "rating": output_row.get("explicit"),
            "axes": axes_map or None,
            "axes_complete": axes_complete(axes_map),
            "xmp": output_row.get("xmp"),
            "verification": verify_xmp_explicit(output_row),
        }

    as_source_block: Dict[str, Any] = {}
    if basename:
        src_row = by_source.get(basename)
        if isinstance(src_row, dict):
            as_source_block = {
                "basename": basename,
                "inferred": src_row.get("inferred"),
                "n": src_row.get("n"),
                "keepers_4plus": src_row.get("keepers_4plus") or src_row.get("favorite_fanout"),
                "contributors": src_row.get("contributors") or [],
            }

    workflow_block: Dict[str, Any] = {}
    graph_hash = output_row.get("graph_hash") if output_row else None
    if isinstance(graph_hash, str) and graph_hash:
        gh_row = by_graph.get(graph_hash)
        if isinstance(gh_row, dict):
            workflow_block = {
                "graph_hash": graph_hash,
                "inferred": gh_row.get("inferred"),
                "n": gh_row.get("n"),
                "keepers_4plus": gh_row.get("keepers_4plus"),
                "catalog_slug": gh_row.get("catalog_slug"),
                "shape_id": gh_row.get("shape_id"),
                "contributors": gh_row.get("contributors") or [],
            }

    recipe_block: Dict[str, Any] = {}
    shape_recipe = output_row.get("shape_recipe") if output_row else None
    if isinstance(shape_recipe, str) and shape_recipe:
        rec_row = by_recipe.get(shape_recipe)
        if isinstance(rec_row, dict):
            recipe_block = {
                "shape_recipe": shape_recipe,
                "inferred": rec_row.get("inferred"),
                "n": rec_row.get("n"),
                "keepers_4plus": rec_row.get("keepers_4plus"),
            }

    sources_cited: List[Dict[str, Any]] = []
    if output_row:
        paths = output_row.get("source_paths") or []
        basenames = output_row.get("sources") or []
        for raw, bn in zip(paths, basenames):
            cited: Dict[str, Any] = {"basename": bn, "via_source": raw}
            src_row = by_source.get(bn) if bn else None
            if isinstance(src_row, dict):
                cited["source_inferred"] = src_row.get("inferred")
                cited["source_n"] = src_row.get("n")
            sources_cited.append(cited)

    rating_effective = None
    if explicit_block.get("rating") is not None:
        rating_effective = explicit_block.get("rating")
    elif as_source_block.get("inferred") is not None:
        rating_effective = as_source_block.get("inferred")
    elif workflow_block.get("inferred") is not None:
        rating_effective = workflow_block.get("inferred")

    return {
        "ok": True,
        "query_relpath": relpath,
        "lookup_keys": keys,
        "basename": basename or None,
        "rating_effective": rating_effective,
        "explicit": explicit_block or None,
        "axes": axes_map or None,
        "as_source": as_source_block or None,
        "workflow": workflow_block or None,
        "recipe": recipe_block or None,
        "sources_cited": sources_cited,
        "index_updated_at": ratings_doc.get("updated_at"),
        "index_stats": ratings_doc.get("stats"),
    }


def show_ratings(
    index: Dict[str, Any],
    *,
    graph_hash: Optional[str] = None,
    source: Optional[str] = None,
    output: Optional[str] = None,
    shape_recipe: Optional[str] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "matches": {}}
    if graph_hash:
        table = index.get("by_graph_hash") or {}
        keys = _prefix_match_keys(table, graph_hash) if len(graph_hash) < 64 else [graph_hash]
        out["matches"]["by_graph_hash"] = {k: table[k] for k in keys if k in table}
    if source:
        table = index.get("by_source_basename") or {}
        bn = normalize_source_basename(source)
        keys = [bn] if bn in table else _prefix_match_keys(table, bn or source)
        out["matches"]["by_source_basename"] = {k: table[k] for k in keys if k in table}
    if output:
        table = index.get("by_output_relpath") or {}
        norm = output.strip().replace("\\", "/").lstrip("/")
        if not norm.startswith("output/") and norm.startswith("og/"):
            norm = f"output/{norm}"
        keys = [norm] if norm in table else _prefix_match_keys(table, norm)
        out["matches"]["by_output_relpath"] = {k: table[k] for k in keys if k in table}
    if shape_recipe:
        table = index.get("by_shape_recipe") or {}
        keys = [shape_recipe] if shape_recipe in table else _prefix_match_keys(table, shape_recipe)
        out["matches"]["by_shape_recipe"] = {k: table[k] for k in keys if k in table}
    if not out["matches"]:
        out["ok"] = False
        out["error"] = "no_lookup_key_provided"
    return out


def default_ratings_index_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "ratings_index.json"


def default_appetite_index_path(og_root: Path) -> Path:
    return og_root.resolve().parent / "_status" / "appetite_index.json"


def _init_appetite_doc() -> Dict[str, Any]:
    return {
        "version": APPETITE_SCHEMA_VERSION,
        "updated_at": utc_now(),
        "by_output_relpath": {},
    }


def _load_or_init_appetite_doc(appetite_index_path: Path) -> Dict[str, Any]:
    if appetite_index_path.is_file():
        try:
            doc = json.loads(appetite_index_path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                doc.setdefault("by_output_relpath", {})
                return doc
        except (OSError, json.JSONDecodeError):
            pass
    return _init_appetite_doc()


def lookup_output_appetite(output_path: str, appetite_doc: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Resolve an appetite row from appetite_index by path variants (mirrors lookup_output_rating)."""
    table = (appetite_doc or {}).get("by_output_relpath") or {}
    if not isinstance(table, dict):
        return None
    raw = str(output_path or "").strip().replace("\\", "/")
    if not raw:
        return None
    keys = [raw, Path(raw).name]
    if "/output/output/" in raw:
        keys.append(re.sub(r"^.*?/output/output/", "output/", raw))
    if "/og/" in raw:
        tail = raw.split("/og/", 1)[-1]
        keys.append(f"output/og/{tail.rstrip('/')}")
        keys.append(f"og/{tail.rstrip('/')}")
    expanded: List[str] = []
    for key in keys:
        key = key.strip().replace("\\", "/")
        if not key:
            continue
        expanded.append(key)
        for suffix in (".mp4", ".MP4", ".png", ".PNG", ".webm", ".WEBM"):
            if key.endswith(suffix):
                expanded.append(key[: -len(suffix)])
    seen: set[str] = set()
    for key in expanded:
        if not key or key in seen:
            continue
        seen.add(key)
        row = table.get(key)
        if isinstance(row, dict):
            return row
    return None


def set_output_appetite(
    *,
    media_abs: Path,
    media_relpath: str,
    appetite: str,
    facet: str = "both",
    og_root: Path,
    appetite_index_path: Path,
) -> Dict[str, Any]:
    """
    Record an appetite ("do more WITH this") + facet for one output in appetite_index.json.

    Appetite is a direction signal, stored separately from the XMP quality star so it
    survives ``ratings build`` (which only rewrites ratings_index.json). ``appetite=""``
    clears the row. Never touches XMP.
    """
    from correlate_output_ratings import output_relpath_keys_from_xmp

    appetite = normalize_appetite(appetite)
    facet = normalize_appetite_facet(facet)
    media_abs = Path(media_abs)
    if not media_abs.is_file():
        raise FileNotFoundError(str(media_abs))
    og_root = Path(og_root).resolve()

    xmp_like = media_abs.with_suffix(".XMP")
    try:
        short_key, discovery_key = output_relpath_keys_from_xmp(xmp_like, og_root)
    except ValueError:
        short_key = ""
        discovery_key = str(media_relpath or "").replace("\\", "/")

    doc = _load_or_init_appetite_doc(appetite_index_path)
    table = doc.setdefault("by_output_relpath", {})

    if not appetite:
        for k in (discovery_key, short_key):
            if k:
                table.pop(k, None)
        cleared = True
    else:
        row: Dict[str, Any] = {
            "appetite": appetite,
            "facet": facet,
            "score": APPETITE_SCORE.get(appetite, 0.0),
            "short_key": short_key,
            "updated_at": utc_now(),
        }
        for k in (discovery_key, short_key):
            if k:
                table[k] = row
        cleared = False

    doc["updated_at"] = utc_now()
    _atomic_write_json_doc(appetite_index_path, doc)

    return {
        "ok": True,
        "relpath": media_relpath,
        "appetite": appetite,
        "facet": facet if appetite else None,
        "cleared": cleared,
        "discovery_key": discovery_key,
        "short_key": short_key,
    }


def cmd_ratings_build(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    jobs_root = Path(args.jobs_root).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve()
    out_path = Path(args.out or default_ratings_index_path(og_root)).expanduser().resolve()
    ffprobe = args.ffprobe or shutil.which("ffprobe")

    doc = build_ratings_index(
        og_root=og_root,
        jobs_root=jobs_root,
        data_root=data_root,
        out_path=out_path,
        name_glob=str(args.name_glob or "*.XMP"),
        days=int(args.days or 0),
        ffprobe=ffprobe,
    )
    stats = doc.get("stats") or {}
    print(f"Wrote {out_path}")
    print(
        "rated_outputs={rated_outputs} with_prompt={with_prompt} with_sources={with_sources} "
        "joined_jobs={joined_shape_factory_jobs} joined_png_graph={joined_png_workflow_graph}".format(
            **{k: stats.get(k, 0) for k in (
                "rated_outputs",
                "with_prompt",
                "with_sources",
                "joined_shape_factory_jobs",
                "joined_png_workflow_graph",
            )}
        )
    )
    print(f"by_graph_hash={len(doc.get('by_graph_hash') or {})} "
          f"by_shape_recipe={len(doc.get('by_shape_recipe') or {})} "
          f"by_source_basename={len(doc.get('by_source_basename') or {})}")
    return 0


def cmd_ratings_show(args: argparse.Namespace) -> int:
    og_root = Path(args.root).expanduser().resolve()
    index_path = Path(args.index or default_ratings_index_path(og_root)).expanduser().resolve()
    if not index_path.is_file():
        print(f"error: ratings index not found: {index_path}", file=__import__("sys").stderr)
        return 1
    index = _load_index(index_path)
    payload = show_ratings(
        index,
        graph_hash=args.graph_hash,
        source=args.source,
        output=args.output,
        shape_recipe=args.shape_recipe,
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def add_ratings_subparser(sub: argparse._SubParsersAction) -> None:
    ratings = sub.add_parser("ratings", help="Build/query inferred ratings index from og/ XMP stars")
    ratings_sub = ratings.add_subparsers(dest="ratings_cmd", required=True)

    build = ratings_sub.add_parser("build", help="Scan rated XMPs and write ratings_index.json")
    build.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    build.add_argument("--jobs-root", default="/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs")
    build.add_argument("--data-root", default="/home/yuji/comfyui-runpod-data")
    build.add_argument("--out", default=None, help="Output JSON (default: <og>/../_status/ratings_index.json)")
    build.add_argument("--name-glob", default="*.XMP")
    build.add_argument("--days", type=int, default=0)
    build.add_argument("--ffprobe", default=None)
    build.set_defaults(func=cmd_ratings_build)

    show = ratings_sub.add_parser("show", help="Look up ratings by graph hash, source, output, or recipe")
    show.add_argument("--root", default="/home/yuji/comfyui-runpod-data/output/og")
    show.add_argument("--index", default=None)
    show.add_argument("--graph-hash", dest="graph_hash", default=None)
    show.add_argument("--source", default=None)
    show.add_argument("--output", default=None)
    show.add_argument("--shape-recipe", dest="shape_recipe", default=None)
    show.set_defaults(func=cmd_ratings_show)
