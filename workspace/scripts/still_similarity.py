#!/usr/bin/env python3
"""Similarity orchestrator: tag overlap + CLIP cosine + optional blend.

Callers pass a provider name; this module does not silently merge providers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from still_components.registry import similarity_providers
from still_embed_index import SqliteEmbedIndex, default_db_path

DEFAULT_PROVIDER = "clip"
BLEND_CLIP_WEIGHT = 0.85
BLEND_TAG_WEIGHT = 0.15


def _norm_cid(content_id: str) -> str:
    return str(content_id or "").strip().lower()


def _hit(
    *,
    content_id: str,
    score: float,
    provider: str,
    reasons: Optional[Sequence[str]] = None,
    model_version: Optional[str] = None,
    shared_tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "content_id": _norm_cid(content_id),
        "score": float(score),
        "reasons": [str(x) for x in (reasons or []) if str(x).strip()],
        "provider": str(provider),
        "model_version": str(model_version).strip() if model_version else None,
        "shared_tags": [str(x) for x in (shared_tags or []) if str(x).strip()],
    }


class SqliteTagSource:
    """TagSource over vision_still_tags sqlite."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)

    def _connect(self):
        from vision_still_tags import connect, default_db_path, ensure_db, load_pin

        db = default_db_path(data_root=self.data_root)
        ensure_db(db)
        con = connect(db)
        fp = list(load_pin().get("fp_blocklist") or [])
        return con, fp

    def tags_for(self, content_id: str) -> Dict[str, List[str]]:
        from vision_still_tags import effective_tags_for_row

        cid = _norm_cid(content_id)
        empty = {"editorial_tags": [], "provisional_tags": [], "effective_tags": []}
        if not cid:
            return empty
        con, fp = self._connect()
        try:
            row = con.execute("SELECT * FROM still_tag_items WHERE content_id=?", (cid,)).fetchone()
            if not row:
                return empty
            return {
                "editorial_tags": _json_list(row["editorial_tags"]),
                "provisional_tags": _json_list(row["provisional_tags"]),
                "effective_tags": effective_tags_for_row(row, fp_blocklist=fp),
            }
        finally:
            con.close()

    def iter_tagged(self) -> Iterable[Dict[str, Any]]:
        from vision_still_tags import effective_tags_for_row

        con, fp = self._connect()
        try:
            rows = con.execute("SELECT * FROM still_tag_items").fetchall()
            for row in rows:
                cid = str(row["content_id"] or "").strip().lower()
                if not cid:
                    continue
                editorial = _json_list(row["editorial_tags"])
                provisional = _json_list(row["provisional_tags"])
                effective = effective_tags_for_row(row, fp_blocklist=fp)
                if not editorial and not provisional and not effective:
                    continue
                yield {
                    "content_id": cid,
                    "editorial_tags": editorial,
                    "provisional_tags": provisional,
                    "effective_tags": effective,
                }
        finally:
            con.close()


def _json_list(raw: Any) -> List[str]:
    import json

    if isinstance(raw, list):
        return [str(x).strip().lower() for x in raw if str(x).strip()]
    if not raw:
        return []
    try:
        val = json.loads(str(raw))
    except Exception:
        return []
    if not isinstance(val, list):
        return []
    return [str(x).strip().lower() for x in val if str(x).strip()]


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa = {str(x).strip().lower() for x in a if str(x).strip()}
    sb = {str(x).strip().lower() for x in b if str(x).strip()}
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    union = sa | sb
    if not union:
        return 0.0
    return float(len(inter) / len(union))


class TagOverlapProvider:
    name = "tags"

    def __init__(self, tag_source: SqliteTagSource) -> None:
        self.tag_source = tag_source

    def find_similar(self, query_id: str, *, limit: int = 24) -> List[Dict[str, Any]]:
        cid = _norm_cid(query_id)
        q = self.tag_source.tags_for(cid)
        q_eff = q.get("effective_tags") or []
        q_ed = q.get("editorial_tags") or []
        if not q_eff and not q_ed:
            return []
        lim = max(1, min(200, int(limit or 24)))
        scored: List[Dict[str, Any]] = []
        for row in self.tag_source.iter_tagged():
            other = str(row.get("content_id") or "")
            if other == cid:
                continue
            eff = row.get("effective_tags") or []
            ed = row.get("editorial_tags") or []
            j_eff = jaccard(q_eff, eff)
            j_ed = jaccard(q_ed, ed) if q_ed and ed else 0.0
            score = (0.7 * j_eff + 0.3 * j_ed) if (q_ed and ed) else j_eff
            if score <= 0:
                continue
            shared = sorted({str(t).lower() for t in q_eff} & {str(t).lower() for t in eff})
            reasons = []
            if shared:
                preview = ", ".join(shared[:8])
                extra = f" +{len(shared) - 8}" if len(shared) > 8 else ""
                reasons.append(f"shared tags: {preview}{extra}")
            reasons.append(f"jaccard={score:.3f}")
            scored.append(
                _hit(
                    content_id=other,
                    score=score,
                    provider=self.name,
                    reasons=reasons,
                    model_version="tag-overlap-v1",
                    shared_tags=shared,
                )
            )
        scored.sort(key=lambda h: (-float(h["score"]), h["content_id"]))
        return scored[:lim]


class ClipCosineProvider:
    name = "clip"

    def __init__(self, index: SqliteEmbedIndex) -> None:
        self.index = index

    def find_similar(self, query_id: str, *, limit: int = 24) -> List[Dict[str, Any]]:
        cid = _norm_cid(query_id)
        query = self.index.get(cid)
        if not query:
            return []
        model_id = str(query.get("model_id") or "")
        hits = []
        for row in self.index.nearest(cid, limit=limit):
            score = float(row.get("score") or 0.0)
            hits.append(
                _hit(
                    content_id=str(row.get("content_id") or ""),
                    score=score,
                    provider=self.name,
                    reasons=[f"cosine={score:.3f}", f"model={model_id}"],
                    model_version=model_id,
                    shared_tags=[],
                )
            )
        return hits


class BlendRankerProvider:
    """Embed-primary blend: CLIP rank with a small tag boost. Not tag-primary."""

    name = "blend"

    def __init__(self, clip: ClipCosineProvider, tags: TagOverlapProvider) -> None:
        self.clip = clip
        self.tags = tags

    def find_similar(self, query_id: str, *, limit: int = 24) -> List[Dict[str, Any]]:
        clip_hits = {h["content_id"]: h for h in self.clip.find_similar(query_id, limit=max(limit * 3, 48))}
        tag_hits = {h["content_id"]: h for h in self.tags.find_similar(query_id, limit=max(limit * 3, 48))}
        if not clip_hits:
            return []
        ids = set(clip_hits) | set(tag_hits)
        out: List[Dict[str, Any]] = []
        for cid in ids:
            ch = clip_hits.get(cid)
            th = tag_hits.get(cid)
            cscore = float(ch["score"]) if ch else 0.0
            tscore = float(th["score"]) if th else 0.0
            score = BLEND_CLIP_WEIGHT * cscore + BLEND_TAG_WEIGHT * tscore
            reasons = []
            if ch:
                reasons.extend(ch.get("reasons") or [])
            if th:
                reasons.extend(th.get("reasons") or [])
            shared = list((th or {}).get("shared_tags") or [])
            out.append(
                _hit(
                    content_id=cid,
                    score=score,
                    provider=self.name,
                    reasons=reasons,
                    model_version=(ch or {}).get("model_version"),
                    shared_tags=shared,
                )
            )
        out.sort(key=lambda h: (-float(h["score"]), h["content_id"]))
        return out[: max(1, min(200, int(limit or 24)))]


class SimilarityOrchestrator:
    def __init__(
        self,
        *,
        data_root: Path,
        default_provider: Optional[str] = None,
    ) -> None:
        self.data_root = Path(data_root)
        self.default_provider = (
            str(default_provider or os.environ.get("STILL_SIMILARITY_DEFAULT") or DEFAULT_PROVIDER).strip()
            or DEFAULT_PROVIDER
        )
        self.tag_source = SqliteTagSource(self.data_root)
        self.index = SqliteEmbedIndex(default_db_path(data_root=self.data_root))
        self.tags = TagOverlapProvider(self.tag_source)
        self.clip = ClipCosineProvider(self.index)
        self.blend = BlendRankerProvider(self.clip, self.tags)
        similarity_providers.register("tags", self.tags)
        similarity_providers.register("clip", self.clip)
        similarity_providers.register("blend", self.blend)

    def available_providers(self) -> List[str]:
        return ["clip", "tags", "blend"]

    def find_similar(
        self,
        query_id: str,
        *,
        provider: Optional[str] = None,
        limit: int = 24,
    ) -> Dict[str, Any]:
        cid = _norm_cid(query_id)
        requested = str(provider or "").strip() or None
        used = requested or self.default_provider
        notes: List[str] = []
        if used not in {"clip", "tags", "blend"}:
            return {
                "ok": False,
                "error": "unknown_provider",
                "detail": used,
                "content_id": cid,
                "provider": used,
                "items": [],
                "notes": notes,
            }
        if not cid:
            return {
                "ok": False,
                "error": "missing_content_id",
                "content_id": cid,
                "provider": used,
                "items": [],
                "notes": notes,
            }

        impl = {"clip": self.clip, "tags": self.tags, "blend": self.blend}[used]
        if used in {"clip", "blend"} and self.index.get(cid) is None:
            notes.append("clip_index_missing_query")
            items: List[Dict[str, Any]] = []
        else:
            items = impl.find_similar(cid, limit=limit)

        if used == "clip" and items:
            _attach_shared_tags(items, self.tag_source, cid)

        catalog = _catalog_meta_for(self.data_root, [h["content_id"] for h in items] + [cid])
        for h in items:
            meta = catalog.get(h["content_id"]) or {}
            h.update({k: v for k, v in meta.items() if k not in h or not h.get(k)})

        stats = self.index.stats()
        return {
            "ok": True,
            "content_id": cid,
            "provider": used,
            "requested_provider": requested,
            "default_provider": self.default_provider,
            "items": items,
            "count": len(items),
            "index": stats,
            "notes": notes,
            "query": catalog.get(cid) or {"content_id": cid},
        }


def _attach_shared_tags(items: List[Dict[str, Any]], source: SqliteTagSource, query_id: str) -> None:
    q = {t.lower() for t in (source.tags_for(query_id).get("effective_tags") or [])}
    if not q:
        return
    for h in items:
        other = {t.lower() for t in (source.tags_for(h["content_id"]).get("effective_tags") or [])}
        shared = sorted(q & other)
        if shared:
            h["shared_tags"] = shared
            preview = ", ".join(shared[:8])
            extra = f" +{len(shared) - 8}" if len(shared) > 8 else ""
            reasons = list(h.get("reasons") or [])
            reasons.append(f"shared tags: {preview}{extra}")
            h["reasons"] = reasons


def _catalog_meta_for(data_root: Path, content_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    from input_still_catalog import (
        default_catalog_path,
        default_input_root,
        resolve_catalog_still_path,
        still_relpath_for_comfy,
    )
    from still_embed_index import extract_content_id

    want = {_norm_cid(x) for x in content_ids if _norm_cid(x)}
    if not want:
        return {}
    cat = default_catalog_path(data_root=data_root)
    out: Dict[str, Dict[str, Any]] = {}
    if not cat.is_file():
        return out
    root = default_input_root()
    import sqlite3

    con = sqlite3.connect(str(cat))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT path FROM stills WHERE path NOT LIKE '%/_factory/%'").fetchall()
    finally:
        con.close()
    for row in rows:
        stored = str(row["path"] or "")
        cid = extract_content_id(stored)
        if not cid or cid not in want or cid in out:
            continue
        resolved = resolve_catalog_still_path(stored, input_root=root)
        if resolved is None:
            continue
        rel = still_relpath_for_comfy(resolved, input_root=root)
        out[cid] = {
            "content_id": cid,
            "path": str(resolved),
            "basename": resolved.name,
            "relpath": rel,
            "url": "/files/" + rel.replace("\\", "/"),
            "thumb_url": "/files/" + rel.replace("\\", "/"),
        }
        if len(out) >= len(want):
            break
    return out


def find_similar_stills(
    content_id: str,
    *,
    data_root: Path,
    provider: Optional[str] = None,
    limit: int = 24,
) -> Dict[str, Any]:
    orch = SimilarityOrchestrator(data_root=data_root)
    return orch.find_similar(content_id, provider=provider, limit=limit)
