#!/usr/bin/env python3
"""Family A/B experiments: locked compare from an exemplar work product.

v1 entry is exemplar-only (``job_key`` and/or ``output_relpath``). Judgment is
distinction-first (catalog disposition + observed effect), not a quality winner.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from shape_factory import load_yaml, requires_by_slot  # type: ignore
from shape_factory_map import resolve_shape_factory_data_root
from shape_factory_queue import (
    _bindings_declared_by_shape,
    _find_job_doc,
    _remap_prompt_profile_binding_for_family,
    _resolve_identity_still_for_shape,
    _resolve_shape_path,
    queue_shape_factory_combo,
    resolve_or_recover_prompt_profile_binding,
)

AB_DIRNAME = "ab_experiments"
AB_SCHEMA = "comfyui-runpod.ab-experiment.v0"

DISPOSITIONS = frozenset(
    {
        "no_distinction",
        "keep_as_variant",
        "improve_base",
        "new_family",
        "inconclusive",
    }
)
DISTINGUISHING = frozenset({"keep_as_variant", "improve_base", "new_family"})
EMBODY_SIDES = frozenset({"a", "b"})

_AB_ID_RE = re.compile(r"^ab_[0-9a-f]{8,}$", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ab_experiments_dir(data_root: Path) -> Path:
    return Path(data_root).expanduser().resolve() / "shape_factory" / AB_DIRNAME


def new_ab_id() -> str:
    return f"ab_{uuid.uuid4().hex[:12]}"


def _atomic_write_json(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def ab_manifest_path(data_root: Path, ab_id: str) -> Path:
    aid = str(ab_id or "").strip()
    if not aid or not _AB_ID_RE.match(aid):
        raise ValueError(f"invalid ab_id: {ab_id!r}")
    return ab_experiments_dir(data_root) / f"{aid}.json"


def load_ab_manifest(data_root: Path, ab_id: str) -> Dict[str, Any]:
    path = ab_manifest_path(data_root, ab_id)
    if not path.is_file():
        raise FileNotFoundError(f"ab experiment not found: {ab_id}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"corrupt ab manifest: {path}")
    return doc


def save_ab_manifest(data_root: Path, doc: Dict[str, Any]) -> Path:
    aid = str(doc.get("ab_id") or "").strip()
    path = ab_manifest_path(data_root, aid)
    doc = dict(doc)
    doc["schema_version"] = AB_SCHEMA
    doc["updated_at"] = utc_now()
    _atomic_write_json(path, doc)
    return path


def list_ab_experiments(
    data_root: Path, *, limit: int = 50, status: Optional[str] = None
) -> List[Dict[str, Any]]:
    root = ab_experiments_dir(data_root)
    if not root.is_dir():
        return []
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for p in root.glob("ab_*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        if status and str(doc.get("status") or "") != status:
            continue
        rows.append((str(doc.get("updated_at") or doc.get("created_at") or ""), doc))
    rows.sort(key=lambda t: t[0], reverse=True)
    return [d for _, d in rows[: max(1, int(limit))]]


def _bindings_from_job(job: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    raw = job.get("bindings") if isinstance(job.get("bindings"), dict) else {}
    for slot, spec in raw.items():
        if isinstance(spec, str) and spec.strip():
            out[str(slot)] = spec.strip()
        elif isinstance(spec, dict):
            path = str(spec.get("path") or "").strip()
            if path:
                out[str(slot)] = path
    return out


def _merge_binding_overrides(
    base: Dict[str, str], overrides: Optional[Dict[str, Any]]
) -> Dict[str, str]:
    out = dict(base)
    if not isinstance(overrides, dict):
        return out
    for slot, spec in overrides.items():
        if isinstance(spec, str) and spec.strip():
            out[str(slot)] = spec.strip()
        elif isinstance(spec, dict):
            path = str(spec.get("path") or "").strip()
            if path:
                out[str(slot)] = path
            elif spec.get("clear") or spec.get("path") in ("", None):
                out.pop(str(slot), None)
    return out


def _shape_required_slots(shape: Dict[str, Any]) -> List[str]:
    req = requires_by_slot(shape)
    return sorted(req.keys())


def _load_shape(data_root: Path, family_slug: str) -> Dict[str, Any]:
    path = _resolve_shape_path(
        data_root / "shapes" / f"{family_slug}.shape.yaml",
        data_root=data_root,
        family_slug=family_slug,
    )
    return load_yaml(path)


def resolve_exemplar_job(
    *,
    data_root: Path,
    output_root: Optional[Path] = None,
    job_key: Optional[str] = None,
    output_relpath: Optional[str] = None,
) -> Tuple[Dict[str, Any], Path, str]:
    """Return (job_doc, job_path, job_key)."""
    key = str(job_key or "").strip()
    rel = str(output_relpath or "").strip().replace("\\", "/")
    if not key and rel:
        try:
            from shape_factory_job_output_index import (
                default_job_output_index_path,
                lookup_by_relpath,
                open_job_output_index,
            )

            og = None
            if output_root is not None:
                cand = Path(output_root) / "og"
                og = cand if cand.is_dir() else Path(output_root)
            if og is not None:
                index_path = default_job_output_index_path(og)
                if index_path.is_file():
                    con = open_job_output_index(index_path)
                    try:
                        row = lookup_by_relpath(con, rel, output_root=output_root)
                    finally:
                        con.close()
                    if isinstance(row, dict):
                        key = str(row.get("job_key") or "").strip()
        except Exception:
            pass
        if not key:
            try:
                from shape_factory_rating_sampler import job_key_guess_from_output_relpath

                key = str(job_key_guess_from_output_relpath(rel) or "").strip()
            except Exception:
                stem = Path(rel).stem
                key = re.sub(r"(?i)_(?:FINAL|PREVIEW)_\d+$", "", stem)
    if not key:
        raise ValueError("exemplar requires job_key or resolvable output_relpath")
    found = _find_job_doc(data_root, key)
    if not found:
        raise FileNotFoundError(f"exemplar job not found: {key}")
    job, job_path = found
    return job, job_path, key


def _side_bindings(
    shared: Dict[str, str],
    side_extra: Optional[Dict[str, Any]],
    *,
    shape: Dict[str, Any],
) -> Dict[str, str]:
    merged = _merge_binding_overrides(shared, side_extra)
    return _bindings_declared_by_shape(shape, merged)


def _missing_required(shape: Dict[str, Any], bindings: Dict[str, str]) -> List[str]:
    missing: List[str] = []
    for slot in _shape_required_slots(shape):
        if not str(bindings.get(slot) or "").strip():
            missing.append(slot)
    return missing


def _job_status_and_outputs(data_root: Path, job_key: Optional[str]) -> Dict[str, Any]:
    key = str(job_key or "").strip()
    if not key:
        return {"job_key": None, "status": "missing", "outputs": []}
    found = _find_job_doc(data_root, key)
    if not found:
        return {"job_key": key, "status": "missing", "outputs": []}
    job, _ = found
    submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
    status = str(submit.get("status") or job.get("status") or "").strip().lower() or "unknown"
    outs: List[str] = []
    for src in (
        submit.get("outputs") if isinstance(submit.get("outputs"), list) else [],
        job.get("outputs") if isinstance(job.get("outputs"), list) else [],
        (job.get("deposit") or {}).get("videos")
        if isinstance(job.get("deposit"), dict)
        else [],
    ):
        for item in src or []:
            p = str(item or "").strip()
            if p and p not in outs:
                outs.append(p)
    return {
        "job_key": key,
        "family_slug": str(job.get("family_slug") or "").strip() or None,
        "status": status,
        "outputs": outs,
        "prompt_id": str(submit.get("prompt_id") or "").strip() or None,
    }


def refresh_ab_status(data_root: Path, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Update status from job_a / job_b terminal states."""
    side_a = _job_status_and_outputs(data_root, (doc.get("job_a") or {}).get("job_key"))
    side_b = _job_status_and_outputs(data_root, (doc.get("job_b") or {}).get("job_key"))
    doc = dict(doc)
    doc["job_a"] = {**(doc.get("job_a") if isinstance(doc.get("job_a"), dict) else {}), **side_a}
    doc["job_b"] = {**(doc.get("job_b") if isinstance(doc.get("job_b"), dict) else {}), **side_b}

    statuses = {side_a.get("status"), side_b.get("status")}
    fail = {"error", "interrupted", "abandoned", "missing"}
    done = {"complete", "success", "deposited"}
    if statuses & fail and not (statuses <= (fail | done | {"unknown", "pending", "running", "submitted"})):
        # keep evaluating
        pass
    if side_a.get("status") in fail or side_b.get("status") in fail:
        if not (side_a.get("outputs") and side_b.get("outputs")):
            doc["status"] = "failed"
        elif side_a.get("outputs") and side_b.get("outputs"):
            doc["status"] = "ready"
    elif side_a.get("outputs") and side_b.get("outputs"):
        doc["status"] = "ready"
    elif side_a.get("status") in done and side_b.get("status") in done:
        doc["status"] = "ready" if (side_a.get("outputs") and side_b.get("outputs")) else "failed"
    elif any(s in {"running", "submitted", "pending", "executing"} for s in statuses):
        doc["status"] = "running"
    else:
        if doc.get("status") not in {"ready", "failed"} and doc.get("judgment"):
            pass
        elif doc.get("status") not in {"ready", "failed"}:
            doc["status"] = "running" if (side_a.get("job_key") and side_b.get("job_key")) else "pending"
    if isinstance(doc.get("judgment"), dict) and doc.get("status") == "ready":
        doc["status"] = "judged"
    return doc


def _stamp_ab_markers_on_outputs(
    *,
    output_root: Path,
    outputs: Sequence[str],
    ab_id: str,
    slot: str,
    disposition: Optional[str] = None,
    observed_effect: Optional[str] = None,
) -> List[Dict[str, Any]]:
    stamped: List[Dict[str, Any]] = []
    if not outputs:
        return stamped
    try:
        import asset_registry as areg
        from shape_factory import default_asset_registry_path
        from shape_factory_markers import connect, markers_path_for_output_root, set_marker
    except Exception:
        return stamped
    root = Path(output_root).expanduser().resolve()
    db = markers_path_for_output_root(root)
    try:
        con = connect(db)
        reg = areg.connect(default_asset_registry_path(root))
    except Exception:
        return stamped
    try:
        for raw in outputs:
            p = Path(str(raw)).expanduser()
            rel = str(raw).replace("\\", "/").lstrip("/")
            try:
                if p.is_absolute():
                    rel = str(p.resolve().relative_to(root)).replace("\\", "/")
                    abs_p = p.resolve()
                else:
                    abs_p = (root / rel).resolve()
            except Exception:
                abs_p = p
            cid = None
            try:
                existing = areg.by_relpath(reg, rel)
                if existing and existing.get("content_id"):
                    cid = str(existing["content_id"])
                elif abs_p.is_file():
                    cid = str(areg.register(reg, abs_p, relpath=rel) or "") or None
            except Exception:
                cid = None
            if not cid:
                continue
            rows = []
            for key, value, src in (
                ("ab.pair_id", ab_id, "job"),
                ("ab.slot", slot, "job"),
            ):
                try:
                    rows.append(set_marker(con, cid, key, value, source=src))
                except Exception as exc:
                    rows.append({"key": key, "error": str(exc)})
            if disposition:
                try:
                    rows.append(set_marker(con, cid, "ab.disposition", disposition, source="human"))
                except Exception as exc:
                    rows.append({"key": "ab.disposition", "error": str(exc)})
            if observed_effect:
                try:
                    rows.append(
                        set_marker(
                            con, cid, "ab.observed_effect", observed_effect[:512], source="human"
                        )
                    )
                except Exception as exc:
                    rows.append({"key": "ab.observed_effect", "error": str(exc)})
            stamped.append({"path": str(abs_p), "content_id": cid, "markers": rows})
    finally:
        try:
            con.close()
        except Exception:
            pass
        try:
            reg.close()
        except Exception:
            pass
    return stamped


def queue_ab_from_exemplar(
    body: Dict[str, Any],
    *,
    repo_root: Path,
    workspace_root: Path,
    output_root: Path,
    comfy_server: str,
) -> Dict[str, Any]:
    """Queue family_a + family_b locked from an exemplar job/output."""
    data_root = resolve_shape_factory_data_root(repo_root=repo_root)
    exemplar_in = body.get("exemplar") if isinstance(body.get("exemplar"), dict) else {}
    job_key = str(
        exemplar_in.get("job_key") or body.get("job_key") or body.get("from_job") or ""
    ).strip()
    output_relpath = str(
        exemplar_in.get("output_relpath")
        or body.get("output_relpath")
        or body.get("from_output")
        or ""
    ).strip()
    if not job_key and not output_relpath:
        raise ValueError("exemplar required (job_key or output_relpath)")

    job, _job_path, resolved_key = resolve_exemplar_job(
        data_root=data_root,
        output_root=output_root,
        job_key=job_key or None,
        output_relpath=output_relpath or None,
    )
    exemplar_family = str(job.get("family_slug") or "").strip()
    family_a = str(body.get("family_a") or body.get("family_slug_a") or "").strip() or exemplar_family
    family_b = str(body.get("family_b") or body.get("family_slug_b") or "").strip()
    if not family_a:
        raise ValueError("family_a could not be resolved from exemplar")
    if not family_b:
        raise ValueError("family_b is required")

    shared = _bindings_from_job(job)
    shared = _merge_binding_overrides(shared, body.get("shared_overrides") or body.get("shared"))
    side_a_extra = body.get("side_a") if isinstance(body.get("side_a"), dict) else {}
    side_b_extra = body.get("side_b") if isinstance(body.get("side_b"), dict) else {}
    # Top-level identity aliases → side_b (candidate often needs the extra still).
    for alias in ("identity_anchor", "source_still", "identity_still"):
        if alias in body and body.get(alias) not in (None, "") and alias not in side_b_extra:
            side_b_extra = dict(side_b_extra)
            side_b_extra[alias if alias != "identity_still" else "identity_anchor"] = body.get(alias)

    knobs = body.get("knobs") if isinstance(body.get("knobs"), dict) else {}
    overrides: Dict[str, Any] = {}
    if isinstance(body.get("overrides"), dict):
        overrides = dict(body["overrides"])
    if knobs:
        params = dict(overrides.get("parameters") or {}) if isinstance(overrides.get("parameters"), dict) else {}
        for k, v in knobs.items():
            if v is None or v == "":
                continue
            params[str(k)] = v
        if params:
            overrides["parameters"] = params

    shape_a = _load_shape(data_root, family_a)
    shape_b = _load_shape(data_root, family_b)
    bindings_a = _side_bindings(shared, side_a_extra, shape=shape_a)
    bindings_b = _side_bindings(shared, side_b_extra, shape=shape_b)

    notes_engine: List[str] = []
    miss_a = _missing_required(shape_a, bindings_a)
    miss_b = _missing_required(shape_b, bindings_b)
    # Replay path will try identity/prompt recovery; only hard-fail obvious gaps that
    # recovery cannot fill when dry-checking — still attempt queue and surface errors.
    if miss_a:
        notes_engine.append(f"family_a may miss bindings before recovery: {', '.join(miss_a)}")
    if miss_b:
        notes_engine.append(f"family_b may miss bindings before recovery: {', '.join(miss_b)}")

    ab_id = str(body.get("ab_id") or "").strip() or new_ab_id()
    dry_run = bool(body.get("dry_run") or False)
    front = bool(body.get("front") or False)
    seed_mode = str(body.get("seed_mode") or "same").strip() or "same"

    def _queue_side(family: str, bindings: Dict[str, str], slot: str) -> Dict[str, Any]:
        shape = shape_a if slot == "a" else shape_b
        next_bindings = dict(bindings)
        notes: List[str] = []
        source_family = exemplar_family
        if source_family and source_family != family:
            next_bindings, remap = _remap_prompt_profile_binding_for_family(
                next_bindings, data_root=data_root, family_slug=family
            )
            if remap:
                notes.append(f"side_{slot} prompt remap: {remap}")
        recovered = None
        if "prompt_profile" in next_bindings or any(
            isinstance(r, dict)
            and str((r.get("binding") or {}).get("type") or "") == "prompt_bundle"
            for r in (shape.get("requires") or [])
        ):
            next_bindings, recovered = resolve_or_recover_prompt_profile_binding(
                next_bindings,
                job=job,
                shape=shape,
                data_root=data_root,
                family=family,
            )
            if recovered:
                notes.append(f"side_{slot} prompt recovered: {recovered}")

        side_body = dict(body)
        for alias in ("identity_anchor", "source_still", "identity_still"):
            if alias in next_bindings:
                side_body[alias] = next_bindings[alias]
        next_bindings, identity_meta = _resolve_identity_still_for_shape(
            shape=shape,
            body=side_body,
            job=job,
            bindings=next_bindings,
            output_abs="",
            workspace_root=workspace_root,
            output_root=output_root,
            data_root=data_root,
        )
        if identity_meta:
            notes.append(f"side_{slot} identity: {identity_meta}")
        next_bindings = _bindings_declared_by_shape(shape, next_bindings)

        construction = {
            "step": "ab_compare",
            "pick_mode": "replay",
            "ab_pair_id": ab_id,
            "ab_slot": slot,
            "ab_exemplar_job_key": resolved_key,
        }
        result = queue_shape_factory_combo(
            family_slug=family,
            bindings=next_bindings,
            combo_key=None,
            data_root=data_root,
            workspace_root=workspace_root,
            output_root=output_root,
            comfy_server=comfy_server,
            front=front,
            dry_run=dry_run,
            force=bool(body.get("force") or False),
            overrides=overrides or None,
            pick_mode="replay",
            construction=construction,
            seed_mode=seed_mode,
            seed_job=job,
            seed_job_path=_job_path,
        )
        if not isinstance(result, dict):
            raise RuntimeError(f"queue side {slot} returned non-object")
        result = dict(result)
        result["_ab_notes"] = notes
        if identity_meta:
            result["identity_anchor"] = identity_meta
        return result

    result_a = _queue_side(family_a, bindings_a, "a")
    result_b = _queue_side(family_b, bindings_b, "b")

    for label, res in (("a", result_a), ("b", result_b)):
        for note in res.get("_ab_notes") or []:
            notes_engine.append(str(note))
        if isinstance(res, dict) and res.get("prompt_profile_remapped"):
            notes_engine.append(f"side_{label} prompt_profile remapped: {res['prompt_profile_remapped']}")

    doc: Dict[str, Any] = {
        "schema_version": AB_SCHEMA,
        "ab_id": ab_id,
        "created_at": utc_now(),
        "status": "pending" if dry_run else "running",
        "label": str(body.get("label") or "").strip() or None,
        "hypothesis": str(body.get("hypothesis") or "").strip() or None,
        "family_a": family_a,
        "family_b": family_b,
        "exemplar": {
            "job_key": resolved_key,
            "output_relpath": output_relpath or None,
            "family_slug": exemplar_family or None,
        },
        "shared": shared,
        "side_a": side_a_extra or {},
        "side_b": side_b_extra or {},
        "knobs": knobs or {},
        "seed_mode": seed_mode,
        "notes_engine": notes_engine,
        "job_a": {
            "job_key": result_a.get("job_key"),
            "ok": bool(result_a.get("ok", True)),
            "error": result_a.get("error") or result_a.get("detail"),
        },
        "job_b": {
            "job_key": result_b.get("job_key"),
            "ok": bool(result_b.get("ok", True)),
            "error": result_b.get("error") or result_b.get("detail"),
        },
        "judgment": None,
        "dry_run": dry_run,
    }
    if not dry_run:
        doc = refresh_ab_status(data_root, doc)
        save_ab_manifest(data_root, doc)
    return {"ok": True, "ab": doc, "result_a": result_a, "result_b": result_b}


def get_ab_experiment(data_root: Path, ab_id: str, *, refresh: bool = True) -> Dict[str, Any]:
    doc = load_ab_manifest(data_root, ab_id)
    if refresh:
        doc = refresh_ab_status(data_root, doc)
        save_ab_manifest(data_root, doc)
    return doc


def judge_ab_experiment(
    data_root: Path,
    ab_id: str,
    judgment: Dict[str, Any],
    *,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    doc = get_ab_experiment(data_root, ab_id, refresh=True)
    disposition = str(judgment.get("catalog_disposition") or judgment.get("disposition") or "").strip()
    if disposition not in DISPOSITIONS:
        raise ValueError(
            f"catalog_disposition must be one of {sorted(DISPOSITIONS)}; got {disposition!r}"
        )
    observed = str(judgment.get("observed_effect") or "").strip()
    embody = str(judgment.get("embody_side") or "").strip().lower()
    notes = str(judgment.get("notes") or "").strip()
    if disposition in DISTINGUISHING:
        if not observed:
            raise ValueError("observed_effect required for distinguishing dispositions")
        if embody not in EMBODY_SIDES:
            raise ValueError("embody_side must be 'a' or 'b' for distinguishing dispositions")
    elif embody and embody not in EMBODY_SIDES:
        raise ValueError("embody_side must be 'a' or 'b' when set")

    payload = {
        "catalog_disposition": disposition,
        "observed_effect": observed or None,
        "embody_side": embody or None,
        "notes": notes or None,
        "judged_at": utc_now(),
    }
    doc["judgment"] = payload
    doc["status"] = "judged"
    save_ab_manifest(data_root, doc)

    stamped: List[Dict[str, Any]] = []
    if output_root is not None:
        for slot, side_key in (("a", "job_a"), ("b", "job_b")):
            side = doc.get(side_key) if isinstance(doc.get(side_key), dict) else {}
            outs = side.get("outputs") if isinstance(side.get("outputs"), list) else []
            stamped.extend(
                _stamp_ab_markers_on_outputs(
                    output_root=output_root,
                    outputs=outs,
                    ab_id=ab_id,
                    slot=slot,
                    disposition=disposition,
                    observed_effect=observed or None,
                )
            )
    return {"ok": True, "ab": doc, "markers_stamped": stamped}


def add_ab_subparser(sub: Any) -> None:
    ab = sub.add_parser("ab-queue", help="Queue a locked family A/B pair from an exemplar job/output")
    ab.add_argument("--from-job", dest="from_job", help="Exemplar job_key")
    ab.add_argument("--from-output", dest="from_output", help="Exemplar output relpath")
    ab.add_argument("--family-a", dest="family_a", help="Base family (defaults to exemplar family)")
    ab.add_argument("--family-b", dest="family_b", required=True, help="Candidate family")
    ab.add_argument("--label", default="", help="Optional label")
    ab.add_argument("--hypothesis", default="", help="Optional hypothesis")
    ab.add_argument("--seed-mode", default="same", choices=["same", "new"], help="Noise seed policy")
    ab.add_argument("--front", action="store_true", help="Queue to front of Comfy")
    ab.add_argument("--dry-run", action="store_true")
    ab.add_argument("--side-b-identity", dest="side_b_identity", help="Path for side_b identity_anchor")
    ab.add_argument("--knob", action="append", default=[], help="knob=value (e.g. overlap=16)")
    ab.add_argument("--server", default=None, help="Comfy server host:port")
    ab.set_defaults(func=_cmd_ab_queue)

    judge = sub.add_parser("ab-judge", help="Record distinction judgment for an A/B experiment")
    judge.add_argument("--id", required=True, dest="ab_id", help="ab_id")
    judge.add_argument(
        "--disposition",
        required=True,
        choices=sorted(DISPOSITIONS),
        help="Catalog distinction call",
    )
    judge.add_argument("--embody", dest="embody_side", choices=["a", "b"], help="Side embodying the effect")
    judge.add_argument("--effect", dest="observed_effect", default="", help="Named observed effect")
    judge.add_argument("--notes", default="", help="Optional notes")
    judge.set_defaults(func=_cmd_ab_judge)

    listing = sub.add_parser("ab-list", help="List recent A/B experiments")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--status", default=None)
    listing.set_defaults(func=_cmd_ab_list)

    show = sub.add_parser("ab-show", help="Show one A/B experiment (refresh status)")
    show.add_argument("--id", required=True, dest="ab_id")
    show.set_defaults(func=_cmd_ab_show)


def _repo_root_from_args(args: argparse.Namespace) -> Path:
    # shape_factory main typically runs with cwd / scripts on path; resolve via this file.
    return Path(__file__).resolve().parents[2]


def _default_runtime_roots() -> Tuple[Path, Path]:
    """workspace_root, output_root — match typical host layout."""
    workspace = Path(os.environ.get("COMFYUI_WORKSPACE", "/home/yuji/comfyui-runpod-data")).expanduser()
    output = Path(os.environ.get("COMFYUI_OUTPUT", str(workspace / "output"))).expanduser()
    return workspace, output


def _cmd_ab_queue(args: argparse.Namespace) -> int:
    from shape_factory import DEFAULT_COMFY_SERVER

    knobs: Dict[str, Any] = {}
    for raw in args.knob or []:
        if "=" not in str(raw):
            continue
        k, v = str(raw).split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        try:
            if "." in v:
                knobs[k] = float(v)
            else:
                knobs[k] = int(v)
        except ValueError:
            knobs[k] = v
    body: Dict[str, Any] = {
        "job_key": args.from_job,
        "output_relpath": args.from_output,
        "family_a": args.family_a,
        "family_b": args.family_b,
        "label": args.label,
        "hypothesis": args.hypothesis,
        "seed_mode": args.seed_mode,
        "front": bool(args.front),
        "dry_run": bool(args.dry_run),
        "knobs": knobs,
    }
    if args.side_b_identity:
        body["identity_anchor"] = args.side_b_identity
    repo = _repo_root_from_args(args)
    workspace, output = _default_runtime_roots()
    out = queue_ab_from_exemplar(
        body,
        repo_root=repo,
        workspace_root=workspace,
        output_root=output,
        comfy_server=str(args.server or DEFAULT_COMFY_SERVER),
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


def _cmd_ab_judge(args: argparse.Namespace) -> int:
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root_from_args(args))
    _workspace, output = _default_runtime_roots()
    out = judge_ab_experiment(
        data_root,
        args.ab_id,
        {
            "catalog_disposition": args.disposition,
            "embody_side": args.embody_side,
            "observed_effect": args.observed_effect,
            "notes": args.notes,
        },
        output_root=output,
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def _cmd_ab_list(args: argparse.Namespace) -> int:
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root_from_args(args))
    rows = list_ab_experiments(data_root, limit=int(args.limit), status=args.status)
    print(json.dumps({"ok": True, "experiments": rows}, indent=2, ensure_ascii=False))
    return 0


def _cmd_ab_show(args: argparse.Namespace) -> int:
    data_root = resolve_shape_factory_data_root(repo_root=_repo_root_from_args(args))
    doc = get_ab_experiment(data_root, args.ab_id, refresh=True)
    print(json.dumps({"ok": True, "ab": doc}, indent=2, ensure_ascii=False))
    return 0
