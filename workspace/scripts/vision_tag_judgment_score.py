#!/usr/bin/env python3
"""
Score tag models from blind human judgments.

Reads:
  vision_tag_judgment_queue.json
  vision_tag_judgments.ndjson

Writes:
  vision_tag_judgment_leaderboard.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from vision_tag_judgment_queue import load_queue

SCHEMA_VERSION = 1
JUDGMENTS_NAME = "vision_tag_judgments.ndjson"
LEADERBOARD_NAME = "vision_tag_judgment_leaderboard.json"
TAG_STATS_NAME = "vision_tag_judgment_tag_stats.json"

# Minimum labeled occurrences before a tag appears in "top" lists.
TAG_TOP_MIN_N = 2
TAG_TOP_LIMIT = 25

# Combo definitions relative to primary cohort_x2 pair (fall back to older ids).
COMBO_SPECS = [
    {
        "id": "base∪large",
        "kind": "union",
        "members": ["cohort_x2_pg_tags", "cohort_x2_pg_large_tags"],
        "fallback_members": ["cohort_pg_tags", "cohort_pg_large_tags"],
    },
    {
        "id": "base∩large",
        "kind": "intersection",
        "members": ["cohort_x2_pg_tags", "cohort_x2_pg_large_tags"],
        "fallback_members": ["cohort_pg_tags", "cohort_pg_large_tags"],
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_judgments(status_dir: Path) -> Dict[str, Dict[str, Any]]:
    path = status_dir / JUDGMENTS_NAME
    by_id: Dict[str, Dict[str, Any]] = {}
    if not path.is_file():
        return by_id
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        sid = str(obj.get("sample_id") or "").strip()
        if sid:
            by_id[sid] = obj
    return by_id


def _resolve_members(spec: Dict[str, Any], available: Set[str]) -> List[str]:
    members = [m for m in spec.get("members") or [] if m in available]
    if len(members) >= 2:
        return members
    fb = [m for m in spec.get("fallback_members") or [] if m in available]
    if len(fb) >= 2:
        return fb
    return members or fb


def _empty_tag_acc() -> Dict[str, Any]:
    return {
        "n_good": 0,
        "n_bad": 0,
        "n_labeled": 0,
        "n_important": 0,
        "n_missing": 0,
        "emitted": 0,
        "good_when_emitted": 0,
        "bad_when_emitted": 0,
        "by_model": {},  # model_id -> {emitted, good, bad}
    }


def build_tag_stats(
    *,
    sample_labels: Dict[str, Dict[str, str]],
    sample_model_tags: Dict[str, Dict[str, Set[str]]],
    sample_emitted_by: Dict[str, Dict[str, List[str]]],
    sample_important: Optional[Dict[str, Set[str]]] = None,
    sample_missing: Optional[Dict[str, Set[str]]] = None,
    min_n: int = TAG_TOP_MIN_N,
    top_limit: int = TAG_TOP_LIMIT,
) -> Dict[str, Any]:
    """
    Per-tag human agreement stats for tuning.

    - commonly_correct / commonly_misidentified / commonly_important
    - commonly_missing: human-added gold tags absent from the model union
    """
    acc: Dict[str, Dict[str, Any]] = defaultdict(_empty_tag_acc)
    sample_important = sample_important or {}
    sample_missing = sample_missing or {}

    all_sids = set(sample_labels.keys()) | set(sample_important.keys()) | set(sample_missing.keys())
    for sid in all_sids:
        labs = sample_labels.get(sid) or {}
        imp = sample_important.get(sid) or set()
        miss = sample_missing.get(sid) or set()
        per_model = sample_model_tags.get(sid) or {}
        emitted_by = sample_emitted_by.get(sid) or {}
        emitters_for: Dict[str, Set[str]] = defaultdict(set)
        for tag, vids in emitted_by.items():
            emitters_for[tag] = set(vids)
        for vid, tags in per_model.items():
            for t in tags:
                emitters_for[t].add(vid)

        for tag in imp:
            acc[tag]["n_important"] += 1
        for tag in miss:
            row = acc[tag]
            row["n_missing"] = int(row.get("n_missing") or 0) + 1

        for tag, lab in labs.items():
            row = acc[tag]
            row["n_labeled"] += 1
            if lab == "good":
                row["n_good"] += 1
            elif lab == "bad":
                row["n_bad"] += 1

            emitters = emitters_for.get(tag) or set()
            if emitters:
                row["emitted"] += 1
                if lab == "good":
                    row["good_when_emitted"] += 1
                elif lab == "bad":
                    row["bad_when_emitted"] += 1
                by_m: Dict[str, Dict[str, int]] = row["by_model"]
                for vid in emitters:
                    m = by_m.setdefault(vid, {"emitted": 0, "good": 0, "bad": 0})
                    m["emitted"] += 1
                    if lab == "good":
                        m["good"] += 1
                    elif lab == "bad":
                        m["bad"] += 1

    by_tag: List[Dict[str, Any]] = []
    for tag, row in acc.items():
        n = int(row["n_labeled"])
        n_good = int(row["n_good"])
        n_bad = int(row["n_bad"])
        n_important = int(row["n_important"])
        n_missing = int(row.get("n_missing") or 0)
        emitted = int(row["emitted"])
        good_rate = (n_good / n) if n else None
        bad_rate = (n_bad / n) if n else None
        fp_rate = (int(row["bad_when_emitted"]) / emitted) if emitted else None
        tp_rate = (int(row["good_when_emitted"]) / emitted) if emitted else None
        by_tag.append(
            {
                "tag": tag,
                "n_labeled": n,
                "n_good": n_good,
                "n_bad": n_bad,
                "n_important": n_important,
                "n_missing": n_missing,
                "good_rate": good_rate,
                "bad_rate": bad_rate,
                "emitted": emitted,
                "good_when_emitted": int(row["good_when_emitted"]),
                "bad_when_emitted": int(row["bad_when_emitted"]),
                "tp_rate": tp_rate,
                "fp_rate": fp_rate,
                "by_model": row["by_model"],
            }
        )

    eligible = [r for r in by_tag if int(r["n_labeled"]) >= min_n]
    commonly_correct = sorted(
        eligible,
        key=lambda r: (-(r.get("good_rate") or -1), -int(r["n_labeled"]), r["tag"]),
    )[:top_limit]
    commonly_misidentified = sorted(
        eligible,
        key=lambda r: (-(r.get("bad_rate") or -1), -int(r["n_labeled"]), r["tag"]),
    )[:top_limit]
    contested = sorted(
        [
            r
            for r in eligible
            if r.get("good_rate") is not None and 0.25 <= float(r["good_rate"]) <= 0.75
        ],
        key=lambda r: (-int(r["n_labeled"]), r["tag"]),
    )[:top_limit]
    commonly_important = sorted(
        [r for r in by_tag if int(r.get("n_important") or 0) >= min_n],
        key=lambda r: (-int(r.get("n_important") or 0), -(r.get("good_rate") or -1), r["tag"]),
    )[:top_limit]
    commonly_missing = sorted(
        [r for r in by_tag if int(r.get("n_missing") or 0) >= 1],
        key=lambda r: (-int(r.get("n_missing") or 0), r["tag"]),
    )[:top_limit]

    def _slim(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for r in rows:
            out.append(
                {
                    "tag": r["tag"],
                    "n_labeled": r["n_labeled"],
                    "n_good": r["n_good"],
                    "n_bad": r["n_bad"],
                    "n_important": r.get("n_important") or 0,
                    "n_missing": r.get("n_missing") or 0,
                    "good_rate": r["good_rate"],
                    "bad_rate": r["bad_rate"],
                    "fp_rate": r["fp_rate"],
                    "tp_rate": r["tp_rate"],
                }
            )
        return out

    return {
        "schema": SCHEMA_VERSION,
        "min_n": min_n,
        "tag_count": len(by_tag),
        "commonly_correct": _slim(commonly_correct),
        "commonly_misidentified": _slim(commonly_misidentified),
        "commonly_important": _slim(commonly_important),
        "commonly_missing": _slim(commonly_missing),
        "contested": _slim(contested),
        "by_tag": sorted(by_tag, key=lambda r: (-int(r["n_labeled"]), r["tag"])),
        "note": (
            "Per-tag human labels from the blind judgment experiment. "
            "Use commonly_misidentified as hard-negatives / blocklist candidates; "
            "commonly_correct as stable core vocabulary; "
            "commonly_important as high-value tags for coverage / recall checks; "
            "commonly_missing as gold FNs for ★ important tags that should have been present."
        ),
    }


def score_tag_judgments(status_dir: Path) -> Dict[str, Any]:
    status_dir = Path(status_dir)
    queue = load_queue(status_dir)
    items = [i for i in (queue.get("items") or []) if isinstance(i, dict)]
    judgments = load_judgments(status_dir)

    # Per-sample model→tags and human labels (occurrence-counted across samples).
    sample_model_tags: Dict[str, Dict[str, Set[str]]] = {}
    sample_labels: Dict[str, Dict[str, str]] = {}
    sample_important: Dict[str, Set[str]] = {}
    sample_missing: Dict[str, Set[str]] = {}
    sample_emitted_by: Dict[str, Dict[str, List[str]]] = {}
    available_models: Set[str] = set()

    judged_samples = 0
    labeled_tag_count = 0
    good_count = 0
    bad_count = 0
    important_tag_count = 0
    missing_tag_count = 0

    for item in items:
        sid = str(item.get("sample_id") or "")
        j = judgments.get(sid)
        if not j:
            continue
        labels = j.get("labels") if isinstance(j.get("labels"), dict) else {}
        raw_imp = j.get("important") if isinstance(j.get("important"), list) else []
        raw_miss = j.get("missing") if isinstance(j.get("missing"), list) else []
        important = {str(t).strip().lower() for t in raw_imp if str(t).strip()}
        missing = {str(t).strip().lower() for t in raw_miss if str(t).strip()}
        good = {str(t).strip().lower() for t, v in labels.items() if str(v).lower() == "good" and str(t).strip()}
        bad = {str(t).strip().lower() for t, v in labels.items() if str(v).lower() == "bad" and str(t).strip()}
        has_labels = bool(good or bad)
        if not has_labels and not important and not missing:
            continue

        emitted_by = item.get("emitted_by") if isinstance(item.get("emitted_by"), dict) else {}
        per_model: Dict[str, Set[str]] = defaultdict(set)
        emitted_norm: Dict[str, List[str]] = {}
        union_tags: Set[str] = set()
        for tag, vids in emitted_by.items():
            t = str(tag).strip().lower()
            if not t:
                continue
            union_tags.add(t)
            vid_list = [str(v) for v in (vids if isinstance(vids, list) else [])]
            emitted_norm[t] = sorted(set(vid_list))
            for vid in vid_list:
                per_model[str(vid)].add(t)
        missing = {t for t in missing if t not in union_tags}
        sample_model_tags[sid] = dict(per_model)
        sample_emitted_by[sid] = emitted_norm
        available_models.update(per_model.keys())

        if has_labels:
            judged_samples += 1
            labeled_tag_count += len(good) + len(bad)
            good_count += len(good)
            bad_count += len(bad)
            sample_labels[sid] = {**{t: "good" for t in good}, **{t: "bad" for t in bad}}
        if important:
            important_tag_count += len(important)
            sample_important[sid] = important
        if missing:
            missing_tag_count += len(missing)
            sample_missing[sid] = missing

    def _important_metrics(model_tag_fn) -> Dict[str, Any]:
        imp_n = imp_hit = 0
        for sid, imp in sample_important.items():
            tags = model_tag_fn(sid)
            if not imp:
                continue
            imp_n += len(imp)
            imp_hit += len(tags & imp)
        return {
            "important_n": imp_n,
            "important_hit": imp_hit,
            "important_recall": (imp_hit / imp_n) if imp_n else None,
        }

    def _missing_metrics(model_tag_fn) -> Dict[str, Any]:
        miss_n = miss_hit = 0
        for sid, miss in sample_missing.items():
            tags = model_tag_fn(sid)
            if not miss:
                continue
            miss_n += len(miss)
            miss_hit += len(tags & miss)
        return {
            "missing_n": miss_n,
            "missing_hit": miss_hit,
            "missing_recall": (miss_hit / miss_n) if miss_n else None,
            "missing_fn": miss_n - miss_hit,
        }

    def _score_emitter(model_tag_fn, *, id: str, kind: str, members: Optional[List[str]] = None) -> Dict[str, Any]:
        tp = fp = model_n = gold_n = 0
        for sid, labs in sample_labels.items():
            tags = model_tag_fn(sid)
            if not tags:
                continue
            good = {t for t, v in labs.items() if v == "good"}
            bad = {t for t, v in labs.items() if v == "bad"}
            model_n += len(tags)
            gold_n += len(good)
            tp += len(tags & good)
            fp += len(tags & bad)
        precision = (tp / model_n) if model_n else None
        recall = (tp / gold_n) if gold_n else None
        f1 = None
        if precision is not None and recall is not None and (precision + recall) > 0:
            f1 = 2 * precision * recall / (precision + recall)
        judged_emitted = tp + fp
        # Extended gold = labeled-good ∪ human-added missing.
        ext_tp = ext_gold = 0
        sids = set(sample_labels.keys()) | set(sample_missing.keys())
        for sid in sids:
            tags = model_tag_fn(sid)
            labs = sample_labels.get(sid) or {}
            good = {t for t, v in labs.items() if v == "good"}
            gold = good | (sample_missing.get(sid) or set())
            if not gold:
                continue
            ext_gold += len(gold)
            ext_tp += len(tags & gold)
        row = {
            "id": id,
            "kind": kind,
            "emitted": model_n,
            "true_positives": tp,
            "false_positives": fp,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "fp_rate_among_judged": (fp / judged_emitted) if judged_emitted else None,
            "gold_good": gold_n,
            "gold_good_covered": tp,
            "extended_recall": (ext_tp / ext_gold) if ext_gold else None,
            "extended_gold": ext_gold,
            "extended_hit": ext_tp,
            **_important_metrics(model_tag_fn),
            **_missing_metrics(model_tag_fn),
        }
        if members is not None:
            row["members"] = members
        return row

    models_out: List[Dict[str, Any]] = []
    for vid in sorted(available_models):
        models_out.append(
            _score_emitter(
                lambda sid, v=vid: (sample_model_tags.get(sid) or {}).get(v) or set(),
                id=vid,
                kind="model",
            )
        )

    combos_out: List[Dict[str, Any]] = []
    for spec in COMBO_SPECS:
        members = _resolve_members(spec, available_models)
        if len(members) < 2:
            continue
        kind = str(spec.get("kind") or "union")

        def _combo_tags(sid: str, _members=members, _kind=kind) -> Set[str]:
            sets = [(sample_model_tags.get(sid) or {}).get(m) or set() for m in _members]
            if _kind == "intersection":
                return set.intersection(*sets) if all(sets) else set()
            return set.union(*sets) if sets else set()

        combos_out.append(
            _score_emitter(_combo_tags, id=str(spec["id"]), kind=kind, members=members)
        )

    tag_stats = build_tag_stats(
        sample_labels=sample_labels,
        sample_model_tags=sample_model_tags,
        sample_emitted_by=sample_emitted_by,
        sample_important=sample_important,
        sample_missing=sample_missing,
    )
    # Leaderboard keeps slim top lists; full by_tag lives in the sidecar file.
    tag_stats_sidecar = {
        **tag_stats,
        "scored_utc": utc_now(),
        "judged_samples": judged_samples,
    }
    tag_stats_path = status_dir / TAG_STATS_NAME
    tag_stats_path.write_text(
        json.dumps(tag_stats_sidecar, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    board = {
        "schema": SCHEMA_VERSION,
        "scored_utc": utc_now(),
        "judged_samples": judged_samples,
        "queue_samples": len(items),
        "labeled_tags": labeled_tag_count,
        "good_tags": good_count,
        "bad_tags": bad_count,
        "important_tags": important_tag_count,
        "missing_tags": missing_tag_count,
        "models": models_out,
        "combos": combos_out,
        "tag_stats": {
            "tag_count": tag_stats["tag_count"],
            "min_n": tag_stats["min_n"],
            "commonly_correct": tag_stats["commonly_correct"],
            "commonly_misidentified": tag_stats["commonly_misidentified"],
            "commonly_important": tag_stats["commonly_important"],
            "commonly_missing": tag_stats["commonly_missing"],
            "contested": tag_stats["contested"],
            "path": str(tag_stats_path),
            "note": tag_stats["note"],
        },
        "note": "Development experiment — not wired into Discovery ratings.",
    }
    out_path = status_dir / LEADERBOARD_NAME
    out_path.write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    board["_path"] = str(out_path)
    board["_tag_stats_path"] = str(tag_stats_path)
    return board


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "  —  "
    return f"{100.0 * x:5.1f}%"


def print_table(board: Dict[str, Any]) -> None:
    rows = list(board.get("models") or []) + list(board.get("combos") or [])
    rows.sort(key=lambda r: (-(r.get("f1") or -1), -(r.get("precision") or -1)))
    print(f"judged_samples={board.get('judged_samples')}  labeled_tags={board.get('labeled_tags')}  missing_tags={board.get('missing_tags')}")
    print(f"{'id':32} {'P':>7} {'R':>7} {'F1':>7} {'ImpR':>7} {'MissN':>6} {'emit':>6}")
    print("-" * 80)
    for r in rows:
        print(
            f"{str(r.get('id')):32} "
            f"{_fmt_pct(r.get('precision'))} "
            f"{_fmt_pct(r.get('recall'))} "
            f"{_fmt_pct(r.get('f1'))} "
            f"{_fmt_pct(r.get('important_recall'))} "
            f"{int(r.get('missing_n') or 0):6d} "
            f"{int(r.get('emitted') or 0):6d}"
        )
    ts = board.get("tag_stats") if isinstance(board.get("tag_stats"), dict) else {}
    mis = list(ts.get("commonly_misidentified") or [])[:10]
    ok = list(ts.get("commonly_correct") or [])[:10]
    imp = list(ts.get("commonly_important") or [])[:10]
    miss = list(ts.get("commonly_missing") or [])[:10]
    if mis or ok or imp or miss:
        print()
        print("commonly_misidentified (top):")
        for r in mis:
            print(
                f"  {r.get('tag'):28} bad={_fmt_pct(r.get('bad_rate'))} "
                f"n={int(r.get('n_labeled') or 0)}"
            )
        print("commonly_correct (top):")
        for r in ok:
            print(
                f"  {r.get('tag'):28} good={_fmt_pct(r.get('good_rate'))} "
                f"n={int(r.get('n_labeled') or 0)}"
            )
        print("commonly_important (top):")
        for r in imp:
            print(
                f"  {r.get('tag'):28} important_n={int(r.get('n_important') or 0)} "
                f"good={_fmt_pct(r.get('good_rate'))}"
            )
        print("commonly_missing (top):")
        for r in miss:
            print(
                f"  {r.get('tag'):28} missing_n={int(r.get('n_missing') or 0)}"
            )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Score vision tag judgments")
    ap.add_argument(
        "--status-dir",
        type=Path,
        default=Path("/home/yuji/comfyui-runpod-data/output/_status"),
    )
    args = ap.parse_args(list(argv) if argv is not None else None)
    board = score_tag_judgments(Path(args.status_dir))
    print_table(board)
    print(json.dumps({"ok": True, "path": board.get("_path")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
