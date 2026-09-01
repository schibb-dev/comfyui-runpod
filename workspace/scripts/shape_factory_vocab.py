#!/usr/bin/env python3
"""Shape Factory station vocabulary: IO class, chain_role, catalog stems.

Descriptive first — operators see it now; automation reads it later.
Not a constraint / lockout engine.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PRIMARY_INPUTS = frozenset({"still", "video"})
INPUT_PROFILES = frozenset(
    {
        "still_prompt",
        "video_prompt",
        "video_identity_still_prompt",
    }
)
CHAIN_ROLES = frozenset(
    {
        "origin",
        "extend",
        "mutate",
        "denouement",
        "standalone",
    }
)

# Process-class tags used in catalog stems and UI badges.
IO_TAGS = frozenset(
    {
        "I2V",
        "V2V",
        "VI2V",
        "EXT",  # legacy synonym → V2V + extend hint
        "II2V",
        "IV2V",
        "I2I",
        "V2I",
        "T2V",
        "VV2V",
    }
)

# New catalog stems: {Brand}_{yyyy-MM-dd}_{HHmmss}_{I2V|V2V|VI2V}_{seq}
_STEM_IO_RE = re.compile(
    r"_(?P<io>I2V|V2V|VI2V|EXT|II2V|IV2V|I2I|V2I|T2V|VV2V)_"
    r"(?P<seq>\d+)\b",
    re.IGNORECASE,
)
_STEM_DATE_RE = re.compile(
    r"^(?P<brand>.+?)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{6})_",
    re.IGNORECASE,
)

PROFILE_TO_IO = {
    "still_prompt": "I2V",
    "video_prompt": "V2V",
    "video_identity_still_prompt": "VI2V",
}

PROFILE_TO_PRIMARY = {
    "still_prompt": "still",
    "video_prompt": "video",
    "video_identity_still_prompt": "video",
}

# Expected required media slots per profile (soft check).
PROFILE_EXPECTED_SLOTS: Dict[str, frozenset[str]] = {
    "still_prompt": frozenset({"source_still", "prompt_profile"}),
    "video_prompt": frozenset({"source_video", "prompt_profile"}),
    "video_identity_still_prompt": frozenset(
        {"source_video", "identity_anchor", "prompt_profile"}
    ),
}

WAN_START_IMAGE_TYPES = frozenset({"WanImageToVideo", "WanImageToVideoMulti"})
LOAD_IMAGE_TYPES = frozenset({"LoadImage", "LoadImageMask", "LoadImageOutput"})
VHS_VIDEO_TYPES = frozenset(
    {
        "VHS_LoadVideo",
        "VHS_LoadVideoPath",
        "VHS_LoadVideoFFmpeg",
        "VHS_LoadVideoFFmpegPath",
    }
)


def io_class_for_profile(input_profile: str) -> Optional[str]:
    return PROFILE_TO_IO.get(str(input_profile or "").strip())


def primary_input_for_profile(input_profile: str) -> Optional[str]:
    return PROFILE_TO_PRIMARY.get(str(input_profile or "").strip())


def normalize_io_tag(tag: str) -> str:
    t = str(tag or "").strip().upper()
    if t == "EXT":
        return "V2V"
    return t


def vocab_fields_from_shape(shape: Dict[str, Any]) -> Dict[str, Any]:
    """Extract station-spec fields (missing keys omitted)."""
    out: Dict[str, Any] = {}
    for key in ("primary_input", "input_profile", "chain_role"):
        val = shape.get(key)
        if val is not None and str(val).strip():
            out[key] = str(val).strip()
    io = shape.get("io_class") or io_class_for_profile(str(out.get("input_profile") or ""))
    if io:
        out["io_class"] = normalize_io_tag(str(io))
    return out


def stamp_job_vocab(job_meta: Dict[str, Any], shape: Dict[str, Any]) -> None:
    """Stamp vocabulary onto a new job dict (in-place). No historical backfill."""
    fields = vocab_fields_from_shape(shape)
    for k, v in fields.items():
        job_meta[k] = v


def validate_shape_vocab(shape: Dict[str, Any]) -> List[str]:
    """Soft + hard field checks on a shape document. Returns error strings."""
    errors: List[str] = []
    primary = str(shape.get("primary_input") or "").strip()
    profile = str(shape.get("input_profile") or "").strip()
    role = str(shape.get("chain_role") or "").strip()

    if not primary:
        errors.append("missing primary_input")
    elif primary not in PRIMARY_INPUTS:
        errors.append(f"invalid primary_input: {primary!r}")

    if not profile:
        errors.append("missing input_profile")
    elif profile not in INPUT_PROFILES:
        errors.append(f"invalid input_profile: {profile!r}")

    if not role:
        errors.append("missing chain_role")
    elif role not in CHAIN_ROLES:
        errors.append(f"invalid chain_role: {role!r}")

    if profile in PROFILE_TO_PRIMARY and primary and PROFILE_TO_PRIMARY[profile] != primary:
        errors.append(
            f"primary_input {primary!r} disagrees with input_profile {profile!r} "
            f"(expected {PROFILE_TO_PRIMARY[profile]!r})"
        )

    expected = PROFILE_EXPECTED_SLOTS.get(profile)
    if expected:
        slots = {
            str(r.get("slot") or "").strip()
            for r in (shape.get("requires") or [])
            if isinstance(r, dict)
        }
        missing = sorted(expected - slots)
        if missing:
            errors.append(f"input_profile {profile!r} missing required slots: {missing}")

    return errors


def parse_catalog_stem(name: str) -> Dict[str, Any]:
    """Parse a catalog / candidate basename for IO tag + sequence.

    Accepts stems with or without ``-readable`` / ``.json`` / ``.candidate.json``.
    Legacy ``EXT`` normalizes to io_class ``V2V`` with ``legacy_ext: true``.
    """
    raw = str(name or "").strip()
    base = Path(raw).name
    for suffix in (
        ".candidate.json",
        "-readable.json",
        ".json",
        ".yaml",
        ".yml",
    ):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base.endswith("-readable"):
        base = base[: -len("-readable")]

    out: Dict[str, Any] = {"stem": base, "ok": False}
    m_io = _STEM_IO_RE.search(base)
    if not m_io:
        out["reason"] = "no_io_tag"
        return out

    tag_raw = m_io.group("io").upper()
    out["io_tag_raw"] = tag_raw
    out["io_class"] = normalize_io_tag(tag_raw)
    out["seq"] = m_io.group("seq")
    out["legacy_ext"] = tag_raw == "EXT"
    if tag_raw == "EXT":
        out["chain_role_hint"] = "extend"

    m_date = _STEM_DATE_RE.match(base)
    if m_date:
        out["brand"] = m_date.group("brand")
        out["date"] = m_date.group("date")
        out["time"] = m_date.group("time")
    out["ok"] = True
    return out


def format_catalog_stem(
    brand: str,
    *,
    date: str,
    time: str,
    io_class: str,
    seq: int | str,
) -> str:
    """Build a new-style catalog stem (no extension)."""
    io = normalize_io_tag(io_class)
    if io == "V2V" and str(io_class).strip().upper() == "EXT":
        io = "V2V"
    if io not in {"I2V", "V2V", "VI2V"} and io not in IO_TAGS:
        raise ValueError(f"unsupported io_class for stem: {io_class!r}")
    seq_s = str(seq).strip()
    if not seq_s.isdigit():
        raise ValueError(f"seq must be numeric: {seq!r}")
    brand_s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(brand).strip()).strip("_") or "Brand"
    return f"{brand_s}_{date}_{time}_{io}_{seq_s.zfill(5) if len(seq_s) < 5 else seq_s}"


def _litegraph_nodes(workflow: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if nid is None:
            continue
        out[str(nid)] = node
    return out


def _litegraph_links(workflow: Dict[str, Any]) -> List[Tuple[str, str, Optional[str]]]:
    """Return (origin_id, target_id, target_input_name_or_None)."""
    nodes = _litegraph_nodes(workflow)
    # Map link_id → origin
    by_id: Dict[Any, Tuple[str, Any]] = {}
    rows: List[Tuple[str, str, Optional[str]]] = []
    for link in workflow.get("links") or []:
        if isinstance(link, list) and len(link) >= 5:
            lid, oid, _oslot, tid, _tslot = link[0], link[1], link[2], link[3], link[4]
            by_id[lid] = (str(oid), tid)
            rows.append((str(oid), str(tid), None))
        elif isinstance(link, dict):
            oid = link.get("origin_id")
            tid = link.get("target_id")
            if oid is None or tid is None:
                continue
            rows.append((str(oid), str(tid), None))
    # Enrich target input names from node.inputs[].link
    enriched: List[Tuple[str, str, Optional[str]]] = []
    for node in nodes.values():
        tid = str(node.get("id"))
        for inp in node.get("inputs") or []:
            if not isinstance(inp, dict):
                continue
            lid = inp.get("link")
            if lid is None:
                continue
            origin = by_id.get(lid)
            if origin:
                enriched.append((origin[0], tid, str(inp.get("name") or "") or None))
            else:
                # Fallback: find from rows
                for oid, t2, _ in rows:
                    if t2 == tid:
                        enriched.append((oid, tid, str(inp.get("name") or "") or None))
                        break
    return enriched or rows


def _upstream_of(
    workflow: Dict[str, Any],
    node_id: str,
    *,
    input_name: Optional[str] = None,
) -> List[str]:
    links = _litegraph_links(workflow)
    out: List[str] = []
    for oid, tid, iname in links:
        if tid != str(node_id):
            continue
        if input_name is not None and iname is not None and iname != input_name:
            continue
        if input_name is not None and iname is None:
            # Ambiguous — include anyway when name unknown
            pass
        out.append(oid)
    return out


def _walk_ancestors(
    workflow: Dict[str, Any],
    start_ids: Sequence[str],
    *,
    max_depth: int = 40,
) -> List[Dict[str, Any]]:
    nodes = _litegraph_nodes(workflow)
    seen: set[str] = set()
    order: List[Dict[str, Any]] = []
    frontier = [str(s) for s in start_ids]
    depth = 0
    while frontier and depth < max_depth:
        nxt: List[str] = []
        for nid in frontier:
            if nid in seen:
                continue
            seen.add(nid)
            node = nodes.get(nid)
            if not node:
                continue
            order.append(
                {
                    "id": nid,
                    "type": str(node.get("type") or node.get("class_type") or ""),
                }
            )
            nxt.extend(_upstream_of(workflow, nid))
        frontier = nxt
        depth += 1
    return order


def wan_start_image_roots(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Locate Wan* nodes and classify start_image ancestry roots."""
    nodes = _litegraph_nodes(workflow)
    results: List[Dict[str, Any]] = []
    for nid, node in nodes.items():
        ntype = str(node.get("type") or node.get("class_type") or "")
        if ntype not in WAN_START_IMAGE_TYPES:
            continue
        upstream = _upstream_of(workflow, nid, input_name="start_image")
        if not upstream:
            # Some graphs omit named inputs in link enrich — try any inbound
            upstream = _upstream_of(workflow, nid)
        ancestors = _walk_ancestors(workflow, upstream)
        root_kinds: List[str] = []
        for anc in ancestors:
            t = anc["type"]
            if t in LOAD_IMAGE_TYPES:
                root_kinds.append("still")
            elif t in VHS_VIDEO_TYPES:
                root_kinds.append("video")
        # Prefer first concrete media root in walk order
        primary_root = None
        for kind in root_kinds:
            primary_root = kind
            break
        results.append(
            {
                "wan_node_id": nid,
                "wan_type": ntype,
                "start_image_from": list(upstream),
                "ancestors": ancestors,
                "media_roots": root_kinds,
                "primary_root": primary_root,
            }
        )
    return results


def validate_start_image_vs_primary_input(
    shape: Dict[str, Any],
    workflow: Optional[Dict[str, Any]] = None,
    *,
    template_path: Optional[Path] = None,
) -> List[str]:
    """Hard check: Wan start_image ancestry must match shape primary_input.

    - primary_input=still → ancestry must include LoadImage* (not only VHS)
    - primary_input=video → ancestry must include VHS video loader
    """
    errors: List[str] = []
    primary = str(shape.get("primary_input") or "").strip()
    if primary not in PRIMARY_INPUTS:
        return errors  # vocab validator covers missing/invalid

    wf = workflow
    if wf is None and template_path is not None:
        path = Path(template_path)
        if not path.is_file():
            errors.append(f"template missing: {path}")
            return errors
        try:
            wf = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"template unreadable: {exc}")
            return errors
    if not isinstance(wf, dict):
        tpl = shape.get("template")
        if tpl:
            return validate_start_image_vs_primary_input(
                shape, template_path=Path(str(tpl))
            )
        errors.append("no workflow/template to validate start_image")
        return errors

    roots = wan_start_image_roots(wf)
    if not roots:
        # Some V2V graphs may not expose Wan start_image the same way — soft skip
        # only when primary is video and a VHS loader exists as source slot.
        errors.append("no WanImageToVideo start_image found in template")
        return errors

    for row in roots:
        media = set(row.get("media_roots") or [])
        if primary == "still":
            if "still" not in media:
                errors.append(
                    f"Wan#{row['wan_node_id']} start_image has no LoadImage ancestry "
                    f"(roots={sorted(media) or ['none']}); primary_input=still"
                )
            # Hard fail if ONLY video (the FB8VB2 bug class)
            if media == {"video"}:
                errors.append(
                    f"Wan#{row['wan_node_id']} start_image is video-only while "
                    f"primary_input=still (label would lie)"
                )
        elif primary == "video":
            if "video" not in media and "still" in media and not media & {"video"}:
                # still-only start_image on a video-primary plate is suspicious
                errors.append(
                    f"Wan#{row['wan_node_id']} start_image is still-only while "
                    f"primary_input=video"
                )
            # For V2V, start_image often comes from last-frame of VHS — require video in chain
            if "video" not in media and "still" not in media:
                errors.append(
                    f"Wan#{row['wan_node_id']} start_image has no media ancestry"
                )
    return errors


def validate_shape_document(
    shape: Dict[str, Any],
    *,
    check_start_image: bool = True,
) -> List[str]:
    errors = validate_shape_vocab(shape)
    if check_start_image:
        errors.extend(validate_start_image_vs_primary_input(shape))
    return errors


def load_workflow_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def guess_io_from_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Heuristic IO / profile guess for corpus proposals (not authoritative)."""
    nodes = _litegraph_nodes(workflow)
    types = [str(n.get("type") or n.get("class_type") or "") for n in nodes.values()]
    has_load_image = any(t in LOAD_IMAGE_TYPES for t in types)
    has_vhs = any(t in VHS_VIDEO_TYPES for t in types)
    roots = wan_start_image_roots(workflow)
    media = set()
    for r in roots:
        media.update(r.get("media_roots") or [])

    # identity: LoadImage feeding CLIPVision while VHS feeds temporal path
    has_clip_vision = any(t == "CLIPVisionEncode" for t in types)
    if has_vhs and has_load_image and has_clip_vision and "video" in media:
        return {
            "io_class": "VI2V",
            "primary_input": "video",
            "input_profile": "video_identity_still_prompt",
            "chain_role_guess": "extend",
        }
    if "still" in media or (has_load_image and not has_vhs):
        return {
            "io_class": "I2V",
            "primary_input": "still",
            "input_profile": "still_prompt",
            "chain_role_guess": "origin",
        }
    if has_vhs or "video" in media:
        return {
            "io_class": "V2V",
            "primary_input": "video",
            "input_profile": "video_prompt",
            "chain_role_guess": "extend",
        }
    return {
        "io_class": None,
        "primary_input": None,
        "input_profile": None,
        "chain_role_guess": "standalone",
    }


def graph_fingerprint_lite(
    workflow: Dict[str, Any], *, include_mode: bool = True
) -> str:
    """Stable structural fingerprint (nodes types+ids + links) — matches factory explorer style.

    ``include_mode=False`` ignores per-node mute/bypass so a run that unmutes
    VHS_VideoCombine (etc.) still matches the enrolled template.

    Note: list order of ``nodes``/``links`` affects this hash. Prefer
    :func:`graph_fingerprint_topology` for family discovery / template matching.
    """
    nodes = []
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        row = {
            "id": node.get("id"),
            "type": node.get("type") or node.get("class_type"),
        }
        if include_mode:
            row["mode"] = node.get("mode", 0)
        nodes.append(row)
    links = []
    for link in workflow.get("links") or []:
        if isinstance(link, list):
            links.append(link[:6])
        elif isinstance(link, dict):
            links.append(
                {
                    k: link.get(k)
                    for k in ("origin_id", "origin_slot", "target_id", "target_slot", "type")
                }
            )
    payload = {"nodes": nodes, "links": links}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Cosmetic / plugin renames that do not change controllable I/O for template review.
_NODE_TYPE_ALIASES: Dict[str, str] = {
    "LoadImageWithFilename": "LoadImage",
    "LoadImageWithFilename|pysssss": "LoadImage",
}


def normalize_node_type(node_type: str, *, aliases: bool = True) -> str:
    t = str(node_type or "").strip()
    if not t:
        return t
    if aliases:
        return _NODE_TYPE_ALIASES.get(t, t)
    return t


def graph_fingerprint_topology(
    workflow: Dict[str, Any], *, aliases: bool = True
) -> str:
    """Id-free topology fingerprint (snowflake-style).

    Hashes the multiset of node types plus typed edges
    ``(src_type, edge_type, dst_type)``. Ignores node ids, link ids, mute/bypass,
    widget values, and array order — so catalog saves and og embeds of the same
    template family collide. Use this for discovery clustering, exemplars, and
    “what can I vary?” template grouping.
    """
    nodes_by_id: Dict[Any, str] = {}
    node_types: List[str] = []
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = normalize_node_type(
            str(node.get("type") or node.get("class_type") or ""), aliases=aliases
        )
        if not node_type:
            continue
        nodes_by_id[node.get("id")] = node_type
        node_types.append(node_type)

    edges: List[Tuple[str, str, str]] = []
    for link in workflow.get("links") or []:
        if isinstance(link, list) and len(link) >= 6:
            src_type = nodes_by_id.get(link[1], "?")
            dst_type = nodes_by_id.get(link[3], "?")
            edge_type = str(link[5])
            edges.append((src_type, edge_type, dst_type))
        elif isinstance(link, dict):
            src_type = nodes_by_id.get(link.get("origin_id"), "?")
            dst_type = nodes_by_id.get(link.get("target_id"), "?")
            edge_type = str(link.get("type") or "")
            edges.append((src_type, edge_type, dst_type))

    payload = {
        "node_types": sorted(Counter(node_types).items()),
        "edges": sorted(edges),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def iter_shape_paths(shapes_dir: Path) -> Iterable[Path]:
    yield from sorted(Path(shapes_dir).glob("*.shape.yaml"))
