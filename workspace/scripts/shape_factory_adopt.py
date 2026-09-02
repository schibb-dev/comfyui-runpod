#!/usr/bin/env python3
"""Adopt an output into Workbench when its embed matches exactly one enrolled shape.

Easy case: topology fingerprint (preferred) or lite fingerprint matches a unique
enrolled family. Reconstruct bindings/prompt instance state (backfill helpers),
mint a synthetic completed ``.job.json``, upsert ``job_output_index``, return ``job_key``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from shape_factory_backfill import (  # noqa: E402
    BACKFILL_MARKER,
    _load_yaml,
    _register_assets,
    _slug,
    reconstruct_bindings,
    synthesize_job,
)
from shape_factory_map import resolve_output_relpath, resolve_shape_factory_data_root  # noqa: E402
from shape_factory_vocab import graph_fingerprint_lite, graph_fingerprint_topology  # noqa: E402

ADOPT_ORIGIN = "adopt_embed"


def _load_workflow_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _companion_png(media_abs: Path) -> Optional[Path]:
    for cand in (
        media_abs.with_suffix(".png"),
        media_abs.with_suffix(".PNG"),
        media_abs.parent / f"{media_abs.stem}.png",
    ):
        if cand.is_file():
            return cand
    return None


def resolve_media_abs(
    *,
    relpath: str,
    output_root: Path,
    workspace_root: Optional[Path] = None,
) -> Path:
    rel = str(relpath or "").replace("\\", "/").strip().lstrip("/")
    if rel.startswith("output/"):
        rel = rel[len("output/") :]
    candidates = [output_root / rel]
    if workspace_root is not None:
        candidates.append(workspace_root / "output" / rel)
        candidates.append(workspace_root / rel)
    for c in candidates:
        if c.is_file():
            return c.resolve()
    abs_p = Path(relpath).expanduser()
    if abs_p.is_file():
        return abs_p.resolve()
    raise FileNotFoundError(f"media not found for relpath={relpath!r}")


def enrolled_shape_matches(shapes_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Map fingerprint → list of enrolled family metas (collisions preserved).

    Indexes topology fingerprints (primary), mode-sensitive/insensitive lite
    fingerprints, plus any ``graph_hash`` stored on the shape YAML.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not shapes_dir.is_dir():
        return out

    def _add(fp: str, meta: Dict[str, Any]) -> None:
        if not fp:
            return
        bucket = out.setdefault(fp, [])
        slug = str(meta.get("family_slug") or "")
        if any(str(m.get("family_slug") or "") == slug for m in bucket):
            return
        bucket.append(dict(meta))

    for path in sorted(shapes_dir.glob("*.shape.yaml")):
        doc = _load_yaml(path)
        if not doc:
            continue
        slug = str(doc.get("family_slug") or path.name[: -len(".shape.yaml")]).strip()
        meta = {
            "family_slug": slug,
            "shape_path": str(path),
            "shape_id": doc.get("shape_id"),
            "graph_hash": doc.get("graph_hash"),
            "io_class": doc.get("io_class"),
            "chain_role": doc.get("chain_role"),
        }
        gh = str(doc.get("graph_hash") or "").strip()
        if gh:
            _add(gh, meta)
        tpl = doc.get("template")
        if tpl:
            try:
                from shape_factory import dockerify_repo_path, hostify_repo_path

                # Host → container when in Docker; identity/hostify otherwise.
                tpl_path = dockerify_repo_path(str(tpl))
                if not tpl_path.is_file():
                    tpl_path = hostify_repo_path(str(tpl))
            except Exception:
                tpl_path = Path(str(tpl)).expanduser()
            wf = _load_workflow_json(tpl_path)
            if wf:
                fp_topo = graph_fingerprint_topology(wf)
                fp = graph_fingerprint_lite(wf)
                fp_im = graph_fingerprint_lite(wf, include_mode=False)
                meta["fingerprint"] = fp_topo
                meta["fingerprint_lite"] = fp
                meta["fingerprint_ignore_mode"] = fp_im
                meta["template_resolved"] = str(tpl_path)
                _add(fp_topo, meta)
                _add(fp, meta)
                _add(fp_im, meta)
    return out


def extract_embed_workflow(media_abs: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Path], str]:
    """Return (ui_workflow, png_path, evidence)."""
    try:
        from comfy_meta_lib import extract_prompt_workflow_from_png_chunks, read_png_text_chunks
        from correlate_output_ratings import extract_workflow_png
    except ImportError:
        return None, None, "import_failed"

    png = _companion_png(media_abs)
    if png is None:
        return None, None, "no_companion_png"

    wf = extract_workflow_png(png)
    if isinstance(wf, dict) and (wf.get("nodes") or wf.get("links") is not None):
        return wf, png, "png_workflow_chunk"

    try:
        chunks = read_png_text_chunks(png)
        _pr, wf2 = extract_prompt_workflow_from_png_chunks(chunks)
        if isinstance(wf2, dict) and (wf2.get("nodes") or wf2.get("links") is not None):
            return wf2, png, "png_text_chunks"
    except Exception:
        pass
    return None, png, "no_ui_workflow"


def match_output_to_shapes(
    *,
    media_abs: Path,
    data_root: Path,
) -> Dict[str, Any]:
    """Easy-case matcher: fingerprint embed → enrolled families."""
    wf, png, evidence = extract_embed_workflow(media_abs)
    if not isinstance(wf, dict):
        return {
            "ok": False,
            "error": "no_ui_workflow",
            "detail": "Companion PNG has no UI workflow chunk to fingerprint.",
            "evidence": evidence,
            "png_path": str(png) if png else None,
            "confidence": "none",
            "matches": [],
        }
    fp_topo = graph_fingerprint_topology(wf)
    fp = graph_fingerprint_lite(wf)
    fp_im = graph_fingerprint_lite(wf, include_mode=False)
    if not fp_topo and not fp and not fp_im:
        return {
            "ok": False,
            "error": "fingerprint_failed",
            "confidence": "none",
            "matches": [],
            "evidence": evidence,
        }
    enrolled = enrolled_shape_matches(data_root / "shapes")
    # Prefer topology hits; fall back to mode-insensitive / lite.
    raw_hits = (
        list(enrolled.get(fp_topo) or [])
        + list(enrolled.get(fp_im) or [])
        + list(enrolled.get(fp) or [])
    )
    by_slug: Dict[str, Dict[str, Any]] = {}
    for m in raw_hits:
        slug = str(m.get("family_slug") or "")
        if slug and slug not in by_slug:
            by_slug[slug] = m
    uniq = list(by_slug.values())
    if not uniq:
        return {
            "ok": False,
            "error": "no_shape_match",
            "detail": "Fingerprint does not match any enrolled shape template.",
            "fingerprint": fp_topo,
            "fingerprint_lite": fp,
            "fingerprint_ignore_mode": fp_im,
            "confidence": "none",
            "matches": [],
            "evidence": evidence,
            "png_path": str(png) if png else None,
        }
    if len(uniq) > 1:
        return {
            "ok": False,
            "error": "ambiguous_shape_match",
            "detail": "Multiple enrolled families share this graph fingerprint — confirm family (not auto-adopt).",
            "fingerprint": fp_topo,
            "fingerprint_lite": fp,
            "fingerprint_ignore_mode": fp_im,
            "confidence": "medium",
            "matches": uniq,
            "evidence": evidence,
            "png_path": str(png) if png else None,
        }
    return {
        "ok": True,
        "error": None,
        "fingerprint": fp_topo,
        "fingerprint_lite": fp,
        "fingerprint_ignore_mode": fp_im,
        "confidence": "high",
        "matches": uniq,
        "family_slug": uniq[0]["family_slug"],
        "evidence": evidence,
        "png_path": str(png) if png else None,
    }


def _existing_job_for_output(
    *,
    data_root: Path,
    output_root: Path,
    output_relpath: str,
) -> Optional[Dict[str, Any]]:
    try:
        from shape_factory_job_output_index import (
            default_job_output_index_path,
            lookup_by_relpath,
            open_job_output_index,
        )
        from shape_factory_queue import _find_job_doc
    except Exception:
        return None
    og = output_root / "og"
    index_path = default_job_output_index_path(og if og.is_dir() else output_root)
    if not index_path.is_file():
        return None
    try:
        con = open_job_output_index(index_path)
        try:
            row = lookup_by_relpath(con, output_relpath, output_root=output_root)
        finally:
            con.close()
    except Exception:
        return None
    if not isinstance(row, dict):
        return None
    job_key = str(row.get("job_key") or "").strip()
    if not job_key:
        return None
    found = _find_job_doc(data_root, job_key)
    if not found:
        return {"job_key": job_key, "family_slug": row.get("family_slug"), "existing": True}
    job, job_path = found
    return {
        "job_key": job_key,
        "family_slug": job.get("family_slug"),
        "job_path": str(job_path),
        "origin": job.get("origin"),
        "existing": True,
    }


def _write_recovered_prompt_profile(
    *,
    data_root: Path,
    family_slug: str,
    job_key: str,
    bindings: Dict[str, Any],
) -> Optional[str]:
    """If prompt_bundle has text only, write a replayable prompt_profile JSON."""
    for slot, row in bindings.items():
        if not isinstance(row, dict):
            continue
        if str(row.get("binding_type") or "") != "prompt_bundle":
            continue
        if row.get("path"):
            return str(row.get("path"))
        pos = str(row.get("positive") or "").strip()
        neg = str(row.get("negative") or "").strip()
        if not pos and not neg:
            return None
        out_dir = data_root / "pools" / family_slug / "prompts" / "_adopt"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{_slug(job_key, 80)}.json"
        doc = {
            "schema_version": "comfyui-runpod.prompt-profile.v0",
            "label": f"adopt:{job_key}",
            "positive": pos,
            "negative": neg,
            "origin": ADOPT_ORIGIN,
        }
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        row["path"] = str(path.resolve())
        row["prompt_text_source"] = "adopt_recovered_file"
        bindings[slot] = row
        return str(path.resolve())
    return None


def adopt_output_easy(
    *,
    relpath: str,
    repo_root: Path,
    output_root: Path,
    workspace_root: Optional[Path],
    family_slug: Optional[str] = None,
    dry_run: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Easy case: unique template match → mint backfill-style job for Workbench.

    If ``family_slug`` is provided and matches one of several ambiguous hits,
    that resolves medium confidence. Auto path requires unique match.
    """
    data_root = resolve_shape_factory_data_root(repo_root=repo_root)
    media_abs = resolve_media_abs(relpath=relpath, output_root=output_root, workspace_root=workspace_root)
    output_relpath = (
        resolve_output_relpath(str(media_abs), output_root, workspace_root=workspace_root)
        or str(relpath).replace("\\", "/").lstrip("/")
    )
    if output_relpath.startswith("output/"):
        output_relpath = output_relpath[len("output/") :]

    existing = _existing_job_for_output(
        data_root=data_root, output_root=output_root, output_relpath=output_relpath
    )
    if existing and not force:
        return {
            "ok": True,
            "adopted": False,
            "already_indexed": True,
            "job_key": existing.get("job_key"),
            "family_slug": existing.get("family_slug"),
            "job_path": existing.get("job_path"),
            "workbench_href": f"/workbench?job={existing.get('job_key')}",
            "detail": "Output already linked to a factory job — open Workbench.",
        }

    match = match_output_to_shapes(media_abs=media_abs, data_root=data_root)
    matches = match.get("matches") if isinstance(match.get("matches"), list) else []
    chosen = None
    if family_slug:
        want = str(family_slug).strip()
        for m in matches:
            if str(m.get("family_slug") or "") == want:
                chosen = m
                break
        if chosen is None and match.get("ok") and match.get("family_slug") == want:
            chosen = matches[0] if matches else None
        if chosen is None:
            return {
                "ok": False,
                "error": "family_not_in_matches",
                "detail": f"Requested family {want!r} is not among fingerprint matches.",
                "match": match,
            }
    elif match.get("ok") and match.get("confidence") == "high" and len(matches) == 1:
        chosen = matches[0]
    else:
        return {
            "ok": False,
            "error": match.get("error") or "not_easy_case",
            "detail": match.get("detail")
            or "Easy adopt requires a unique enrolled template match (or explicit family_slug).",
            "match": match,
        }

    slug = str(chosen.get("family_slug") or "").strip()
    shape_path = Path(str(chosen.get("shape_path") or data_root / "shapes" / f"{slug}.shape.yaml"))
    shape_doc = _load_yaml(shape_path)
    if not shape_doc:
        return {"ok": False, "error": "shape_missing", "shape_path": str(shape_path)}

    ffprobe = shutil.which("ffprobe")
    bindings, evidence = reconstruct_bindings(
        output_abs=media_abs,
        shape_doc=shape_doc,
        workspace_root=workspace_root,
        output_root=output_root,
        ffprobe=ffprobe,
    )
    has_source = any(
        isinstance(b, dict) and b.get("binding_type") != "prompt_bundle" and b.get("path")
        for b in bindings.values()
    )
    if not has_source:
        return {
            "ok": False,
            "error": "unreconstructable_bindings",
            "detail": "Matched template but could not recover source still/video bindings from embed.",
            "match": match,
            "bindings": bindings,
            "evidence": evidence,
        }

    pools_path = data_root / "pools" / slug / "pools.yaml"
    job = synthesize_job(
        output_abs=media_abs,
        output_relpath=output_relpath,
        family_slug=slug,
        shape_doc=shape_doc,
        shape_path=shape_path,
        pools_path=pools_path if pools_path.is_file() else None,
        bindings=bindings,
        asset_ids={},
        evidence={**evidence, "adopt": match.get("evidence"), "fingerprint": match.get("fingerprint")},
    )
    job["origin"] = ADOPT_ORIGIN
    job["adopt"] = {
        "relpath": output_relpath,
        "fingerprint": match.get("fingerprint"),
        "confidence": "high" if not family_slug else match.get("confidence") or "confirmed",
        "png_path": match.get("png_path"),
    }
    job_key = str(job.get("job_key") or "")
    # Prefer adopt-prefixed key for clarity when minting via this path.
    if BACKFILL_MARKER in job_key:
        stem = Path(output_relpath).stem
        job_key = _slug(f"{slug}__{ADOPT_ORIGIN}__{stem}", 160)
        job["job_key"] = job_key

    prompt_path = _write_recovered_prompt_profile(
        data_root=data_root, family_slug=slug, job_key=job_key, bindings=bindings
    )
    job["bindings"] = bindings
    if prompt_path:
        evidence["prompt_profile_path"] = prompt_path

    # Recover owned recipe deltas (prompt text, params, LoRAs) from the embed when present.
    embed_wf, _, embed_ev = extract_embed_workflow(media_abs)
    recipe = _attach_owned_recipe_from_sources(
        job,
        data_root=data_root,
        ui_workflow=embed_wf if isinstance(embed_wf, dict) else None,
        bindings=bindings,
    )
    evidence["recipe"] = recipe
    evidence["embed_workflow"] = embed_ev

    job_path = data_root / "shape_factory" / "jobs" / slug / f"{job_key}.job.json"
    if dry_run:
        return {
            "ok": True,
            "adopted": False,
            "dry_run": True,
            "job_key": job_key,
            "family_slug": slug,
            "job": job,
            "match": match,
            "workbench_href": f"/workbench?job={job_key}",
        }

    import asset_registry as areg
    from shape_factory import default_asset_registry_path

    reg_path = default_asset_registry_path(output_root)
    con = areg.connect(reg_path)
    try:
        asset_ids = _register_assets(
            con,
            output_abs=media_abs,
            output_relpath=output_relpath,
            bindings=bindings,
            workspace_root=workspace_root,
            job_key=job_key,
        )
    finally:
        con.close()
    job["backfill"] = {
        "created_at": job.get("created_at"),
        "evidence": evidence,
        "asset_content_ids": asset_ids,
    }
    job["adopt"]["asset_content_ids"] = asset_ids
    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    try:
        from shape_factory_job_output_index import (
            default_job_output_index_path,
            open_job_output_index,
            upsert_from_job,
        )

        og = output_root / "og"
        index_path = default_job_output_index_path(og if og.is_dir() else output_root)
        idx = open_job_output_index(index_path)
        try:
            upsert_from_job(idx, job, job_path=job_path, output_root=output_root, commit=True)
        finally:
            idx.close()
    except Exception as exc:
        evidence["index_upsert_error"] = str(exc)

    return {
        "ok": True,
        "adopted": True,
        "job_key": job_key,
        "family_slug": slug,
        "job_path": str(job_path),
        "output_relpath": output_relpath,
        "match": match,
        "prompt_profile_path": prompt_path,
        "workbench_href": f"/workbench?job={job_key}",
    }


def _attach_owned_recipe_from_sources(
    job: Dict[str, Any],
    *,
    data_root: Path,
    ui_workflow: Optional[Dict[str, Any]] = None,
    api_prompt: Optional[Dict[str, Any]] = None,
    bindings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fill job-owned prompt / params / loras from embed UI workflow and/or API prompt."""
    out: Dict[str, Any] = {"prompt": False, "params": False, "loras": False}

    # Owned prompt from bindings or API CLIP text (best-effort via bindings recovery).
    try:
        from shape_factory_owned_prompt import (
            ensure_owned_prompt_from_bindings,
            get_owned_prompt,
            merge_owned_prompt,
        )

        if get_owned_prompt(job) is None:
            ensure_owned_prompt_from_bindings(job, data_root=data_root)
        owned = get_owned_prompt(job)
        if owned is None and isinstance(bindings, dict):
            for row in bindings.values():
                if not isinstance(row, dict):
                    continue
                if str(row.get("binding_type") or "") != "prompt_bundle":
                    continue
                pos = str(row.get("positive") or "")
                neg = str(row.get("negative") or "")
                if pos or neg:
                    merge_owned_prompt(job, {"positive": pos, "negative": neg, "label": "adopt-recovered"})
                    out["prompt"] = True
                    break
        elif owned is not None:
            out["prompt"] = True
    except Exception as exc:
        out["prompt_error"] = str(exc)

    # Params from UI workflow (LiteGraph) when available.
    try:
        from shape_factory_owned_params import extract_params_from_workflow

        if isinstance(ui_workflow, dict):
            params = extract_params_from_workflow(ui_workflow)
            if params:
                adhoc = job.get("adhoc_overrides") if isinstance(job.get("adhoc_overrides"), dict) else {}
                adhoc = dict(adhoc)
                prev = adhoc.get("parameters") if isinstance(adhoc.get("parameters"), dict) else {}
                merged = dict(prev)
                merged.update(params)
                adhoc["parameters"] = merged
                job["adhoc_overrides"] = adhoc
                timings = job.get("timings") if isinstance(job.get("timings"), dict) else {}
                timings = dict(timings)
                wl = timings.get("workload") if isinstance(timings.get("workload"), dict) else {}
                wl = dict(wl)
                for k in ("frames", "steps", "overlap"):
                    if k in params:
                        wl[k] = params[k]
                timings["workload"] = wl
                job["timings"] = timings
                out["params"] = True
                out["params_values"] = params
    except Exception as exc:
        out["params_error"] = str(exc)

    # LoRAs from UI workflow preferred; fall back to API prompt Power Lora inputs.
    try:
        from shape_factory_owned_loras import (
            attach_content_hash,
            extract_loras_from_api_prompt,
            extract_loras_from_workflow,
        )

        entries: List[Dict[str, Any]] = []
        node_id: Any = None
        if isinstance(ui_workflow, dict):
            entries, node_id = extract_loras_from_workflow(ui_workflow)
        if not entries and isinstance(api_prompt, dict):
            entries, node_id = extract_loras_from_api_prompt(api_prompt)
            try:
                node_id = int(node_id) if node_id is not None else None
            except (TypeError, ValueError):
                pass
        if entries:
            owned_l = {
                "node_id": node_id,
                "frozen": False,
                "entries": entries,
                "origin": "adopt_or_claim",
            }
            attach_content_hash(owned_l)
            job["loras"] = owned_l
            out["loras"] = True
            out["loras_count"] = len(entries)
    except Exception as exc:
        out["loras_error"] = str(exc)

    return out


def claim_queue_prompt_as_job(
    *,
    prompt_id: str,
    repo_root: Path,
    output_root: Path,
    workspace_root: Optional[Path],
    comfy_server: str,
    family_slug: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Mint a factory job from a live/history Comfy prompt so recipe edits survive OOM.

    Fingerprints the API prompt (converted to a pseudo-litegraph when needed via
    enrolled template match on workflow_name / family hint, else unique topo match
    when a companion UI workflow is unavailable — API-only path uses family_slug
    or unique enrolled match from any embedded workflow_name metadata).
    """
    from shape_factory_work_products import _comfy_queue_entries  # type: ignore

    data_root = resolve_shape_factory_data_root(repo_root=repo_root)
    pid = str(prompt_id or "").strip()
    if not pid:
        return {"ok": False, "error": "missing_prompt_id"}

    # Already a factory job with this prompt_id?
    found_path = None
    found_job = None
    jobs_root = data_root / "shape_factory" / "jobs"
    if jobs_root.is_dir():
        for path in jobs_root.glob("*/*.job.json"):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(loaded, dict):
                continue
            submit = loaded.get("submit") if isinstance(loaded.get("submit"), dict) else {}
            if str(submit.get("prompt_id") or "").strip() == pid:
                found_path, found_job = path, loaded
                break
    if isinstance(found_job, dict) and found_path is not None:
        return {
            "ok": True,
            "claimed": False,
            "already_indexed": True,
            "job_key": found_job.get("job_key"),
            "family_slug": found_job.get("family_slug"),
            "job_path": str(found_path),
            "workbench_href": f"/workbench?job={found_job.get('job_key')}",
            "detail": "Prompt already linked to a factory job.",
        }

    # Fetch queue + history for the prompt payload.
    import urllib.request

    server = str(comfy_server or "").rstrip("/")
    if not server:
        return {"ok": False, "error": "missing_comfy_server"}

    api_prompt: Optional[Dict[str, Any]] = None
    status = "queued"
    job_key_hint = ""
    try:
        with urllib.request.urlopen(f"{server}/queue", timeout=10) as resp:
            queue_doc = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        queue_doc = None
        queue_err = str(exc)
    else:
        queue_err = None
    if isinstance(queue_doc, dict):
        for ent in _comfy_queue_entries(queue_doc.get("queue_running"), status="running") + _comfy_queue_entries(
            queue_doc.get("queue_pending"), status="queued"
        ):
            if ent.get("prompt_id") == pid:
                status = str(ent.get("status") or status)
                job_key_hint = str(ent.get("job_key") or "")
                prompt_obj = ent.get("prompt")
                if isinstance(prompt_obj, dict):
                    # Queue rows sometimes nest {prompt: {…nodes}} or are the node map itself.
                    if any(isinstance(v, dict) and v.get("class_type") for v in prompt_obj.values()):
                        api_prompt = prompt_obj
                    elif isinstance(prompt_obj.get("prompt"), dict):
                        api_prompt = prompt_obj.get("prompt")  # type: ignore[assignment]
                break

    if api_prompt is None:
        try:
            with urllib.request.urlopen(f"{server}/history/{pid}", timeout=15) as resp:
                hist = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return {
                "ok": False,
                "error": "prompt_not_found",
                "detail": f"Not on queue and history fetch failed: {exc}",
                "queue_error": queue_err,
            }
        entry = hist.get(pid) if isinstance(hist, dict) else None
        if not isinstance(entry, dict):
            return {"ok": False, "error": "prompt_not_found", "detail": "Prompt not on queue or history."}
        status = "complete"
        prompt_field = entry.get("prompt")
        # Comfy history prompt is often [number, prompt_id, prompt_dict, extra, outputs_ids]
        if isinstance(prompt_field, list) and len(prompt_field) >= 3 and isinstance(prompt_field[2], dict):
            api_prompt = prompt_field[2]
        elif isinstance(prompt_field, dict):
            api_prompt = prompt_field

    if not isinstance(api_prompt, dict) or not api_prompt:
        return {"ok": False, "error": "missing_api_prompt", "detail": "Could not load Comfy prompt graph."}

    # Resolve family: explicit → unique enrolled match is not available for API-only
    # without UI fingerprint; use family_slug or workflow_name hint.
    slug = str(family_slug or "").strip()
    if not slug and job_key_hint:
        # factory job_keys are usually FAMILY__…
        slug = job_key_hint.split("__", 1)[0].strip()
    if not slug:
        # Try extra_pnginfo / workflow_name keys inside prompt extras if present.
        for node in api_prompt.values():
            if not isinstance(node, dict):
                continue
            meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
            title = str(meta.get("title") or "")
            if title and title.upper() == title and "-" in title:
                slug = title
                break
    if not slug:
        return {
            "ok": False,
            "error": "family_required",
            "detail": "Pass family_slug — API prompts cannot uniquely fingerprint without a UI workflow.",
        }

    shape_path = data_root / "shapes" / f"{slug}.shape.yaml"
    shape_doc = _load_yaml(shape_path)
    if not shape_doc:
        return {"ok": False, "error": "shape_missing", "family_slug": slug, "shape_path": str(shape_path)}

    stem = _slug(f"{slug}__claim__{pid[:12]}", 160)
    job_key = stem
    pools_path = data_root / "pools" / slug / "pools.yaml"
    # Minimal synthetic job (no output yet — claim is for in-flight / history recovery).
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    job: Dict[str, Any] = {
        "schema_version": "comfyui-runpod.shape-job.v0",
        "origin": "claim_queue",
        "created_at": now,
        "family_slug": slug,
        "shape_id": shape_doc.get("shape_id"),
        "graph_hash": shape_doc.get("graph_hash"),
        "shape_path": str(shape_path),
        "template_path": str(shape_doc.get("template") or ""),
        "pools_path": str(pools_path) if pools_path.is_file() else None,
        "job_key": job_key,
        "bindings": {},
        "deposits": shape_doc.get("deposits") if isinstance(shape_doc.get("deposits"), dict) else {},
        "submit": {
            "status": status if status in {"queued", "running", "complete", "error"} else "queued",
            "prompt_id": pid,
            "prompt_source": "claim_queue",
            "claimed_at": now,
        },
        "claim": {"prompt_id": pid, "comfy_server": server},
        "warnings": [],
    }

    recipe = _attach_owned_recipe_from_sources(
        job,
        data_root=data_root,
        api_prompt=api_prompt,
        bindings=job.get("bindings") if isinstance(job.get("bindings"), dict) else {},
    )

    job_path = data_root / "shape_factory" / "jobs" / slug / f"{job_key}.job.json"
    if dry_run:
        return {
            "ok": True,
            "claimed": False,
            "dry_run": True,
            "job_key": job_key,
            "family_slug": slug,
            "job": job,
            "recipe": recipe,
            "workbench_href": f"/workbench?job={job_key}",
        }

    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "claimed": True,
        "job_key": job_key,
        "family_slug": slug,
        "job_path": str(job_path),
        "prompt_id": pid,
        "status": status,
        "recipe": recipe,
        "workbench_href": f"/workbench?job={job_key}",
    }


def add_adopt_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "adopt-from-output",
        help="Easy case: if embed matches one enrolled shape, mint a Workbench job",
    )
    p.add_argument("--relpath", required=True, help="Output relpath under output/ (e.g. og/…/clip.mp4)")
    p.add_argument("--family", dest="family_slug", default=None, help="Required only when match is ambiguous")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="Mint even if output already indexed")
    p.add_argument(
        "--output-root",
        default="/home/yuji/comfyui-runpod-data/output",
        help="Comfy output root",
    )
    p.add_argument(
        "--workspace-root",
        default="/home/yuji/comfyui-runpod-data",
        help="Workspace root (for input/ resolution)",
    )
    p.set_defaults(func=_cmd_adopt)

    pc = sub.add_parser(
        "claim-from-queue",
        help="Mint a Workbench job from a Comfy prompt_id (queue/history) so recipe edits survive OOM",
    )
    pc.add_argument("--prompt-id", required=True)
    pc.add_argument("--family", dest="family_slug", default=None, help="Family slug (required when not inferable)")
    pc.add_argument("--server", default="http://127.0.0.1:8188")
    pc.add_argument("--dry-run", action="store_true")
    pc.add_argument("--output-root", default="/home/yuji/comfyui-runpod-data/output")
    pc.add_argument("--workspace-root", default="/home/yuji/comfyui-runpod-data")
    pc.set_defaults(func=_cmd_claim)


def _cmd_adopt(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[2]
    out = adopt_output_easy(
        relpath=str(args.relpath),
        repo_root=repo,
        output_root=Path(args.output_root),
        workspace_root=Path(args.workspace_root),
        family_slug=args.family_slug,
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


def _cmd_claim(args: argparse.Namespace) -> int:
    repo = Path(__file__).resolve().parents[2]
    out = claim_queue_prompt_as_job(
        prompt_id=str(args.prompt_id),
        repo_root=repo,
        output_root=Path(args.output_root),
        workspace_root=Path(args.workspace_root),
        comfy_server=str(args.server),
        family_slug=args.family_slug,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1
