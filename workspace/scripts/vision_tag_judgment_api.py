#!/usr/bin/env python3
"""
API helpers for the blind tag-judgment experiment.

Storage under status dir:
  vision_tag_judgment_queue.json
  vision_tag_judgments.ndjson
  vision_tag_judgment_leaderboard.json
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from vision_tag_judgment_queue import QUEUE_NAME, load_queue
from vision_tag_judgment_score import (
    JUDGMENTS_NAME,
    LEADERBOARD_NAME,
    load_judgments,
    score_tag_judgments,
)

_LOCK = threading.Lock()

# Prefill unmarked chips from chronic history (still overridable).
DEFAULT_PRIOR_MIN_N = 2
DEFAULT_BAD_RATE = 0.75
DEFAULT_GOOD_RATE = 0.75


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_tag_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for t in raw:
        tag = str(t).strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    out.sort()
    return out


def build_label_priors(
    judgments: Dict[str, Dict[str, Any]],
    *,
    min_n: int = DEFAULT_PRIOR_MIN_N,
    bad_rate: float = DEFAULT_BAD_RATE,
    good_rate: float = DEFAULT_GOOD_RATE,
) -> Dict[str, Any]:
    """
    Derive sticky label defaults from past judgments.

    Tags with enough history and high bad_rate / good_rate become default-bad /
    default-good on new samples (still overridable).
    """
    counts: Dict[str, Dict[str, int]] = {}
    for j in judgments.values():
        labs = j.get("labels") if isinstance(j.get("labels"), dict) else {}
        for raw_t, raw_v in labs.items():
            tag = str(raw_t).strip().lower()
            lab = str(raw_v).strip().lower()
            if not tag or lab not in ("good", "bad"):
                continue
            row = counts.setdefault(tag, {"good": 0, "bad": 0})
            row[lab] += 1

    default_bad: Dict[str, Dict[str, Any]] = {}
    default_good: Dict[str, Dict[str, Any]] = {}
    for tag, row in counts.items():
        n = int(row["good"]) + int(row["bad"])
        if n < min_n:
            continue
        br = float(row["bad"]) / float(n)
        gr = float(row["good"]) / float(n)
        meta = {
            "n_labeled": n,
            "n_bad": int(row["bad"]),
            "n_good": int(row["good"]),
            "bad_rate": br,
            "good_rate": gr,
        }
        if br >= bad_rate:
            default_bad[tag] = {"label": "bad", **meta}
        elif gr >= good_rate:
            default_good[tag] = {"label": "good", **meta}

    return {
        "min_n": min_n,
        "bad_rate_threshold": bad_rate,
        "good_rate_threshold": good_rate,
        "default_bad": default_bad,
        "default_good": default_good,
        "default_bad_tags": sorted(default_bad.keys()),
        "default_good_tags": sorted(default_good.keys()),
    }


def load_leaderboard(status_dir: Path) -> Optional[Dict[str, Any]]:
    path = Path(status_dir) / LEADERBOARD_NAME
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _public_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Strip scoring-only fields for the blind UI."""
    out = {k: v for k, v in item.items() if k not in ("emitted_by", "variant_ids")}
    return out


def get_tag_judgment_payload(status_dir: Path) -> Dict[str, Any]:
    status_dir = Path(status_dir)
    queue = load_queue(status_dir)
    items_raw = [i for i in (queue.get("items") or []) if isinstance(i, dict)]
    judgments = load_judgments(status_dir)
    done_ids = sorted(judgments.keys())

    important_vocab: Set[str] = set()
    missing_vocab: Set[str] = set()
    for j in judgments.values():
        for t in _norm_tag_list(j.get("important")):
            important_vocab.add(t)
        for t in _norm_tag_list(j.get("missing")):
            missing_vocab.add(t)

    priors = build_label_priors(judgments)
    default_bad_tags: Set[str] = set(priors.get("default_bad_tags") or [])
    default_good_tags: Set[str] = set(priors.get("default_good_tags") or [])

    items_public = [_public_item(i) for i in items_raw]
    for it in items_public:
        sid = str(it.get("sample_id") or "")
        j = judgments.get(sid)
        tag_set = {str(t).strip().lower() for t in (it.get("tags") or []) if str(t).strip()}
        # Missing pass: ★ important tags absent from the union — judge which should have been here.
        it["missing_candidates"] = sorted(important_vocab - tag_set)

        if j:
            labs = j.get("labels") if isinstance(j.get("labels"), dict) else {}
            it["labels"] = labs or None
            it["suggested_labels"] = None
            it["judged_utc"] = j.get("judged_utc")
            it["skipped"] = bool(j.get("skipped"))
            it["important"] = sorted(
                {t for t in _norm_tag_list(j.get("important")) if t in tag_set}
            )
            # Missing tags are defined as not-in-union; drop any that later appeared in tags.
            it["missing"] = sorted(
                {t for t in _norm_tag_list(j.get("missing")) if t not in tag_set}
            )
        else:
            it["labels"] = None
            it["skipped"] = False
            it["important"] = sorted(important_vocab & tag_set)
            it["missing"] = []
            suggested: Dict[str, str] = {}
            for t in sorted(tag_set & default_bad_tags):
                suggested[t] = "bad"
            for t in sorted(tag_set & default_good_tags):
                if t not in suggested:
                    suggested[t] = "good"
            it["suggested_labels"] = suggested or None

    board = load_leaderboard(status_dir)
    return {
        "ok": True,
        "schema": 1,
        "queue": {
            "built_utc": queue.get("built_utc"),
            "seed": queue.get("seed"),
            "variants": queue.get("variants") or [],
            "candidate_count": queue.get("candidate_count"),
            "item_count": len(items_public),
            "note": queue.get("note"),
        },
        "items": items_public,
        "done_sample_ids": done_ids,
        "done_count": len(done_ids),
        "total_count": len(items_public),
        "important_vocabulary": sorted(important_vocab),
        "missing_vocabulary": sorted(missing_vocab),
        "label_priors": priors,
        "leaderboard": board,
        "min_score_samples": 15,
        "note": "Blind tag judgment experiment — model names hidden until scored.",
    }


def save_tag_judgment(
    status_dir: Path,
    body: Dict[str, Any],
    *,
    auto_score: bool = True,
) -> Dict[str, Any]:
    status_dir = Path(status_dir)
    status_dir.mkdir(parents=True, exist_ok=True)
    sid = str(body.get("sample_id") or "").strip()
    if not sid:
        return {"ok": False, "error": "missing_sample_id"}

    labels_in = body.get("labels") if isinstance(body.get("labels"), dict) else {}
    labels: Dict[str, str] = {}
    for k, v in labels_in.items():
        tag = str(k).strip().lower()
        lab = str(v).strip().lower()
        if not tag:
            continue
        if lab in ("good", "bad"):
            labels[tag] = lab

    important = _norm_tag_list(body.get("important"))
    missing = _norm_tag_list(body.get("missing"))
    # Missing must not overlap important-in-union labels noise; also drop empties.
    # (UI keeps missing out of the sample tag union; belt-and-suspenders here.)
    missing = [t for t in missing if t not in labels or labels.get(t) != "bad"]

    skipped = bool(body.get("skipped"))
    row = {
        "sample_id": sid,
        "asset_relpath": str(body.get("asset_relpath") or ""),
        "t0": body.get("t0"),
        "t1": body.get("t1"),
        "slice": str(body.get("slice") or "window"),
        "labels": labels,
        "important": important,
        "missing": missing,
        "skipped": skipped,
        "judged_utc": utc_now(),
        "schema": 1,
    }

    # Merge: if client omits important/missing/labels keys entirely, keep prior values.
    path = status_dir / JUDGMENTS_NAME
    with _LOCK:
        existing = load_judgments(status_dir)
        prev = existing.get(sid) if isinstance(existing.get(sid), dict) else None
        if prev:
            if "labels" not in body and isinstance(prev.get("labels"), dict):
                row["labels"] = prev["labels"]
            if "important" not in body and isinstance(prev.get("important"), list):
                row["important"] = _norm_tag_list(prev.get("important"))
            if "missing" not in body and isinstance(prev.get("missing"), list):
                row["missing"] = _norm_tag_list(prev.get("missing"))
        existing[sid] = row
        lines = [json.dumps(existing[k], ensure_ascii=False) for k in sorted(existing.keys())]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        board = None
        if auto_score and len(existing) >= 1:
            try:
                board = score_tag_judgments(status_dir)
                board.pop("_path", None)
            except Exception as e:
                return {
                    "ok": True,
                    "saved": row,
                    "done_count": len(existing),
                    "leaderboard": None,
                    "score_error": str(e),
                }

    return {
        "ok": True,
        "saved": row,
        "done_count": len(existing),
        "leaderboard": board,
    }
