#!/usr/bin/env python3
"""
Shared Danbooru / PromptGen tag-list helpers for the tag-judgment experiment.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence


def normalize_tag_display(raw: str) -> str:
    t = re.sub(r"\s+", " ", str(raw or "")).strip().lower()
    return t


def parse_danbooru_tags(caption: str, *, max_tags: int = 64) -> List[str]:
    """
    Prefer comma-separated Danbooru / PromptGen tag lists.
    Rejects long prose clauses that only happen to contain commas.
    """
    text = (caption or "").strip()
    if not text or text.startswith("[dry-run]"):
        return []
    out: List[str] = []
    seen = set()
    if text.count(",") < 2:
        return []
    for part in text.split(","):
        t = normalize_tag_display(part)
        if not t or len(t) < 2 or len(t) > 48 or t in seen:
            continue
        if len(t.split()) > 4:
            continue
        if re.search(r"[.!?;:]", t):
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_tags:
            break
    if len(out) < 3:
        return []
    long_frac = sum(1 for t in out if len(t) > 28) / float(len(out))
    if long_frac > 0.35:
        return []
    return out


def tags_from_row(row: dict, *, max_tags: int = 64) -> List[str]:
    """Prefer caption parse; fall back to stored tags list if it looks Danbooru-like."""
    cap_tags = parse_danbooru_tags(str(row.get("caption") or ""), max_tags=max_tags)
    if cap_tags:
        return cap_tags
    raw = row.get("tags")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    seen = set()
    for item in raw:
        t = normalize_tag_display(str(item))
        if not t or len(t) < 2 or len(t) > 48 or t in seen:
            continue
        if len(t.split()) > 4:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_tags:
            break
    return out if is_tag_list_like(out) else []


def is_tag_list_like(tags: Sequence[str]) -> bool:
    if len(tags) < 3:
        return False
    long = sum(1 for t in tags if len(t) > 40) / float(len(tags))
    wordy = sum(1 for t in tags if len(t.split()) > 4) / float(len(tags))
    return long <= 0.25 and wordy <= 0.25


def sample_id_for(
    *,
    asset_relpath: str,
    t0: float,
    t1: float,
    slice_name: str,
) -> str:
    a = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(asset_relpath or "").replace("\\", "/"))
    return f"{a}__{float(t0):.3f}_{float(t1):.3f}_{slice_name or 'window'}"
