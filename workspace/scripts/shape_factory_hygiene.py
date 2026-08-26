#!/usr/bin/env python3
"""Workflow hygiene tools for Florence + preview-output cleanup."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Optional

import yaml

from remove_florence_and_automatic_prompt import remove_florence_and_automatic_prompt
from snowflake_factory import strip_video_previews_and_redirect_outputs


DEFAULT_SHAPES_DIR = Path("/home/yuji/src/comfyui-runpod/.data/shapes")
_PREVIEW_TITLE_TOKENS = ("preview", "debug", "raw", "sample frame", "interpoled", "upscaled", "upint")


def _shape_paths(shapes_dir: Path, *, family: str = "") -> list[Path]:
    out: list[Path] = []
    fam = str(family or "").strip()
    for path in sorted(shapes_dir.glob("*.shape.yaml")):
        if not fam:
            out.append(path)
            continue
        try:
            shape = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if str(shape.get("family_slug") or path.stem) == fam:
            out.append(path)
    return out


def _final_node_ids(shape: dict[str, Any]) -> set[int]:
    finals: set[int] = set()
    for prod in shape.get("produces") or []:
        if not isinstance(prod, dict):
            continue
        binding = prod.get("binding") if isinstance(prod.get("binding"), dict) else {}
        nid = binding.get("node_id")
        try:
            if nid is not None:
                finals.add(int(nid))
        except Exception:
            continue
    return finals


def _count_florence(workflow: dict[str, Any]) -> tuple[int, int]:
    nodes = workflow.get("nodes") if isinstance(workflow.get("nodes"), list) else []
    groups = workflow.get("groups") if isinstance(workflow.get("groups"), list) else []
    florence_nodes = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or node.get("class_type") or "")
        props = node.get("properties") if isinstance(node.get("properties"), dict) else {}
        blob = (ntype + " " + json.dumps(props, ensure_ascii=False)).lower()
        if "florence" in blob:
            florence_nodes += 1
    automatic_prompt_groups = 0
    for group in groups:
        if not isinstance(group, dict):
            continue
        title = str(group.get("title") or "").strip().lower()
        if title == "automatic prompt":
            automatic_prompt_groups += 1
    return florence_nodes, automatic_prompt_groups


def _count_preview_issues(workflow: dict[str, Any], *, final_ids: set[int]) -> int:
    issues = 0
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or node.get("class_type") or "")
        if ntype not in {"VHS_VideoCombine", "SaveImage", "SaveAnimatedWEBP", "SaveAnimatedPNG"}:
            continue
        title = str(node.get("title") or "").lower()
        mode = int(node.get("mode", 0) or 0)
        widgets = node.get("widgets_values") if isinstance(node.get("widgets_values"), dict) else {}
        save_output = widgets.get("save_output") if isinstance(widgets, dict) else None
        has_preview_title = any(tok in title for tok in _PREVIEW_TITLE_TOKENS)
        if ntype == "VHS_VideoCombine":
            try:
                nid = int(node.get("id"))
            except Exception:
                nid = -1
            if isinstance(widgets, dict) and "videopreview" in widgets:
                issues += 1
            if final_ids and nid not in final_ids and save_output is not False:
                issues += 1
            if has_preview_title and (mode not in (2, 4) or save_output is not False):
                issues += 1
        else:
            if mode not in (2, 4):
                issues += 1
    return issues


def _apply_hygiene(workflow: dict[str, Any], *, final_ids: set[int]) -> tuple[dict[str, Any], dict[str, int]]:
    w = remove_florence_and_automatic_prompt(copy.deepcopy(workflow))
    changes = strip_video_previews_and_redirect_outputs(
        w,
        "hygiene/%date:yyyy-MM-dd%/template",
        final_node_ids=final_ids or None,
    )
    florence_nodes, automatic_prompt_groups = _count_florence(w)
    return w, {
        "stripped_video_previews": int(changes.get("stripped_video_previews") or 0),
        "redirected_outputs": int(changes.get("redirected_outputs") or 0),
        "disabled_non_final_outputs": int(changes.get("disabled_non_final_outputs") or 0),
        "residual_florence_nodes": florence_nodes,
        "residual_automatic_prompt_groups": automatic_prompt_groups,
    }


def cmd_hygiene_templates(args: argparse.Namespace) -> int:
    shapes_dir = Path(args.shapes_dir).expanduser().resolve()
    include_candidates = bool(getattr(args, "include_candidates", False))
    apply = bool(getattr(args, "apply", False))
    family = str(getattr(args, "family", "") or "").strip()
    limit = int(getattr(args, "limit", 0) or 0)
    rows: list[dict[str, Any]] = []
    changed = 0

    shape_paths = _shape_paths(shapes_dir, family=family)
    for shape_path in shape_paths:
        try:
            shape = yaml.safe_load(shape_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        fam = str(shape.get("family_slug") or shape_path.stem)
        finals = _final_node_ids(shape)
        slots = ["template", "candidate"] if include_candidates else ["template"]
        for slot in slots:
            ref = shape.get(slot)
            if not ref:
                continue
            wf_path = Path(str(ref)).expanduser()
            if not wf_path.is_file():
                continue
            try:
                workflow = json.loads(wf_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            florence_nodes, automatic_prompt_groups = _count_florence(workflow)
            preview_issues = _count_preview_issues(workflow, final_ids=finals)
            issue_count = florence_nodes + automatic_prompt_groups + preview_issues
            rec = {
                "family": fam,
                "slot": slot,
                "path": str(wf_path),
                "issues": {
                    "florence_nodes": florence_nodes,
                    "automatic_prompt_groups": automatic_prompt_groups,
                    "preview_output_issues": preview_issues,
                },
                "changed": False,
            }
            if apply and issue_count > 0:
                cleaned, change_info = _apply_hygiene(workflow, final_ids=finals)
                if (
                    change_info["residual_florence_nodes"] == 0
                    and change_info["residual_automatic_prompt_groups"] == 0
                ):
                    wf_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
                    rec["changed"] = True
                    rec["changes"] = change_info
                    changed += 1
                else:
                    rec["error"] = "residual_hygiene_issues"
                    rec["changes"] = change_info
            rows.append(rec)
            if limit > 0 and len(rows) >= limit:
                break
        if limit > 0 and len(rows) >= limit:
            break

    if bool(getattr(args, "json", False)):
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "templates",
                    "apply": apply,
                    "changed": changed,
                    "count": len(rows),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"# hygiene templates apply={apply} include_candidates={include_candidates}")
    print(f"count={len(rows)} changed={changed}")
    for row in rows:
        issues = row.get("issues") or {}
        print(
            f"- {row.get('family')} [{row.get('slot')}] "
            f"flo={issues.get('florence_nodes')} auto={issues.get('automatic_prompt_groups')} "
            f"preview={issues.get('preview_output_issues')} changed={row.get('changed')}"
        )
    return 0


def cmd_hygiene_jobs(args: argparse.Namespace) -> int:
    # local import avoids circular import when shape_factory imports this module.
    from shape_factory import submit_job_file, unqueue_to_pending

    jobs_dir = Path(args.jobs_dir).expanduser().resolve()
    apply = bool(getattr(args, "apply", False))
    resubmit = bool(getattr(args, "resubmit", False))
    server = str(getattr(args, "server", "") or "").rstrip("/")
    data_root = Path(args.data_root).expanduser().resolve()
    statuses = {
        x.strip().lower()
        for x in str(getattr(args, "statuses", "pending,queued") or "pending,queued").split(",")
        if x.strip()
    }
    report: dict[str, Any] = {
        "ok": True,
        "mode": "jobs",
        "apply": apply,
        "resubmit": resubmit,
        "scanned": 0,
        "eligible": 0,
        "affected": 0,
        "queued_demoted": 0,
        "queued_running_skip": 0,
        "patched": 0,
        "resubmitted": 0,
        "errors": [],
    }

    for job_path in sorted(jobs_dir.rglob("*.job.json")):
        report["scanned"] += 1
        try:
            job = json.loads(job_path.read_text(encoding="utf-8"))
        except Exception as exc:
            report["errors"].append(f"read_job:{job_path}:{exc}")
            continue
        submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
        status = str(submit.get("status") or "").strip().lower()
        if status not in statuses:
            continue
        report["eligible"] += 1

        wf_path = Path(str(job.get("generated_workflow_path") or "")).expanduser()
        if not wf_path.is_file():
            continue
        try:
            workflow = json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception as exc:
            report["errors"].append(f"read_wf:{wf_path}:{exc}")
            continue
        final_ids = _final_node_ids(
            (yaml.safe_load(Path(str(job.get("shape_path") or "")).expanduser().read_text(encoding="utf-8")) or {})
            if str(job.get("shape_path") or "").strip()
            else {}
        )
        florence_nodes, automatic_prompt_groups = _count_florence(workflow)
        preview_issues = _count_preview_issues(workflow, final_ids=final_ids)
        if florence_nodes + automatic_prompt_groups + preview_issues <= 0:
            continue
        report["affected"] += 1
        if not apply:
            continue

        key = str(job.get("job_key") or job_path.stem.replace(".job", ""))
        live_server = str(submit.get("comfy_server") or server).rstrip("/")
        if status == "queued":
            pid = str(submit.get("prompt_id") or "").strip()
            if pid:
                try:
                    unq = unqueue_to_pending(
                        prompt_id=pid,
                        server=live_server,
                        data_root=data_root,
                        job_path=job_path,
                        timeout_s=20,
                    )
                except Exception as exc:
                    report["errors"].append(f"unqueue_exc:{key}:{exc}")
                    continue
                if not unq.get("ok") and str(unq.get("error") or "") == "still_running":
                    report["queued_running_skip"] += 1
                    continue
                if not unq.get("ok"):
                    report["errors"].append(f"unqueue_fail:{key}:{unq.get('error')}")
                    continue
                report["queued_demoted"] += 1

        cleaned, _change_info = _apply_hygiene(workflow, final_ids=final_ids)
        wf_path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        report["patched"] += 1

        if resubmit:
            try:
                out = submit_job_file(
                    job_path,
                    server=live_server,
                    data_root=data_root,
                    dry_run=False,
                    force=False,
                    pending_only=False,
                    client_id="shape_factory",
                    front=False,
                    timeout=int(getattr(args, "timeout", 60)),
                    convert_timeout=int(getattr(args, "convert_timeout", 180)),
                    ignore_quarantine=False,
                    quarantine_path=Path(getattr(args, "quarantine_path")).expanduser().resolve(),
                )
                if out.get("ok") and not out.get("skipped"):
                    report["resubmitted"] += 1
                else:
                    report["errors"].append(f"resubmit_skip:{key}:{out.get('reason') or out}")
            except Exception as exc:
                report["errors"].append(f"resubmit_exc:{key}:{exc}")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def add_hygiene_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    hyg = sub.add_parser("hygiene", help="Workflow hygiene tools (Florence + preview-output cleanup)")
    hyg_sub = hyg.add_subparsers(dest="hygiene_cmd", required=True)

    t = hyg_sub.add_parser("templates", help="Scan/apply hygiene on shape-linked template workflows")
    t.add_argument("--shapes-dir", default=str(DEFAULT_SHAPES_DIR))
    t.add_argument("--family", default="", help="Optional family_slug filter")
    t.add_argument("--include-candidates", action="store_true", help="Also process shape candidate workflows")
    t.add_argument("--apply", action="store_true", help="Write cleaned workflows in-place")
    t.add_argument("--limit", type=int, default=0, help="Optional max files to process")
    t.add_argument("--json", action="store_true", help="Emit JSON output")
    t.set_defaults(func=cmd_hygiene_templates, hygiene_cmd="templates")

    j = hyg_sub.add_parser("jobs", help="Scan/apply hygiene on job-generated workflows")
    j.add_argument("--jobs-dir", default="/home/yuji/src/comfyui-runpod/.data/shape_factory/jobs")
    j.add_argument("--statuses", default="pending,queued", help="Comma-separated submit statuses to target")
    j.add_argument("--apply", action="store_true", help="Write cleaned workflows in-place")
    j.add_argument("--resubmit", action="store_true", help="Resubmit cleaned jobs after patching")
    j.add_argument("--server", default="http://127.0.0.1:8188")
    j.add_argument("--data-root", default="/home/yuji/comfyui-runpod-data")
    j.add_argument("--timeout", type=int, default=60)
    j.add_argument("--convert-timeout", type=int, default=180)
    j.add_argument("--quarantine-path", default="/home/yuji/src/comfyui-runpod/.data/shape_factory/quarantine.json")
    j.set_defaults(func=cmd_hygiene_jobs, hygiene_cmd="jobs")
