#!/usr/bin/env python3
"""Shared creation-control façade for automation and manual job shaping."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional


def _append_control_event(
    *,
    data_root: Path,
    action: str,
    job_key: Optional[str],
    job_path: Optional[Path],
    actor: Optional[str],
    source_surface: Optional[str],
    reason: Optional[str],
    ok: bool,
) -> None:
    """Best-effort audit trail for shared creation-control mutations."""
    import shape_factory as sf

    path: Optional[Path] = None
    doc: Optional[dict[str, Any]] = None
    if job_path is not None and Path(job_path).is_file():
        path = Path(job_path)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            doc = loaded if isinstance(loaded, dict) else None
        except Exception:
            doc = None
    elif job_key:
        p, j = sf.find_job_by_key(data_root, str(job_key))
        if p is not None and isinstance(j, dict):
            path, doc = p, j
    if path is None or not isinstance(doc, dict):
        return
    submit = doc.get("submit") if isinstance(doc.get("submit"), dict) else {}
    if not isinstance(submit, dict):
        submit = {}
        doc["submit"] = submit
    hist = submit.get("flow_events")
    if not isinstance(hist, list):
        hist = []
        submit["flow_events"] = hist
    hist.append(
        {
            "at": sf.utc_now(),
            "action": str(action or "").strip(),
            "actor": str(actor or "operator").strip() or "operator",
            "source_surface": str(source_surface or "api").strip() or "api",
            "reason": str(reason or "").strip() or None,
            "ok": bool(ok),
        }
    )
    sf.atomic_write_json(path, doc)


def mutate_job(
    *,
    action: str,
    data_root: Path,
    server: str = "",
    job_key: Optional[str] = None,
    job_path: Optional[Path] = None,
    timeout_s: int = 15,
    finish_action: str = "later",
    front: bool = False,
    dry_run: bool = False,
    expunge: bool = False,
    prompt_id: Optional[str] = None,
    reason: Optional[str] = None,
    actor: Optional[str] = None,
    source_surface: Optional[str] = None,
    skip_first_frames: Optional[int] = None,
    frame_load_cap: Optional[int] = None,
    mark_in: Optional[float] = None,
    mark_out: Optional[float] = None,
    slot: Optional[str] = None,
    binding_path: Optional[str] = None,
) -> dict[str, Any]:
    """Mutate/edit an existing flow job using one canonical control entrypoint."""
    import shape_factory as sf

    root = Path(data_root).expanduser().resolve()
    act = str(action or "").strip().lower()
    control = {
        "actor": str(actor or "operator").strip() or "operator",
        "reason": str(reason or "").strip() or None,
        "source_surface": str(source_surface or "api").strip() or "api",
    }
    out: dict[str, Any]
    if act == "begin_edit":
        out = sf.begin_job_edit(
            data_root=root,
            server=server,
            job_key=job_key,
            job_path=job_path,
            timeout_s=timeout_s,
        )
        out["control"] = control
        _append_control_event(
            data_root=root,
            action=act,
            job_key=job_key or str(out.get("job_key") or "").strip() or None,
            job_path=job_path,
            actor=actor,
            source_surface=source_surface,
            reason=reason,
            ok=bool(out.get("ok")),
        )
        return out
    if act == "finish_edit":
        fin = str(finish_action or "").strip().lower() or "later"
        if fin not in {"later", "cancel", "now"}:
            return {"ok": False, "error": "bad_action", "detail": "finish_action must be later|cancel|now"}
        out = sf.finish_job_edit(
            data_root=root,
            action=fin,
            server=server,
            job_key=job_key,
            job_path=job_path,
            front=front,
            dry_run=dry_run,
        )
        out["control"] = control
        _append_control_event(
            data_root=root,
            action=act,
            job_key=job_key or str(out.get("job_key") or "").strip() or None,
            job_path=job_path,
            actor=actor,
            source_surface=source_surface,
            reason=reason,
            ok=bool(out.get("ok")),
        )
        return out
    if act == "update_trim":
        out = sf.update_pending_job_vhs_window(
            data_root=root,
            skip_first_frames=int(skip_first_frames or 0),
            frame_load_cap=int(frame_load_cap or 0),
            mark_in=mark_in,
            mark_out=mark_out,
            server=server,
            job_key=job_key,
            job_path=job_path,
        )
        out["control"] = control
        _append_control_event(
            data_root=root,
            action=act,
            job_key=job_key or str(out.get("job_key") or "").strip() or None,
            job_path=job_path,
            actor=actor,
            source_surface=source_surface,
            reason=reason,
            ok=bool(out.get("ok")),
        )
        return out
    if act == "update_binding":
        out = sf.update_pending_job_binding_path(
            data_root=root,
            slot=str(slot or ""),
            binding_path=str(binding_path or ""),
            server=server,
            job_key=job_key,
            job_path=job_path,
        )
        out["control"] = control
        _append_control_event(
            data_root=root,
            action=act,
            job_key=job_key or str(out.get("job_key") or "").strip() or None,
            job_path=job_path,
            actor=actor,
            source_surface=source_surface,
            reason=reason,
            ok=bool(out.get("ok")),
        )
        return out
    if act == "discard":
        out = sf.discard_pending_job(
            data_root=root,
            server=server,
            job_key=job_key,
            job_path=job_path,
            expunge=bool(expunge),
            reason=reason,
        )
        out["control"] = control
        _append_control_event(
            data_root=root,
            action=act,
            job_key=job_key or str(out.get("job_key") or "").strip() or None,
            job_path=job_path,
            actor=actor,
            source_surface=source_surface,
            reason=reason,
            ok=bool(out.get("ok")),
        )
        return out
    if act == "unqueue_to_pending":
        if not server:
            return {"ok": False, "error": "bad_request", "detail": "unqueue_to_pending requires server"}
        pid = str(prompt_id or "").strip()
        if not pid and job_key:
            _p, job = sf.find_job_by_key(root, job_key)
            if isinstance(job, dict):
                submit = job.get("submit") if isinstance(job.get("submit"), dict) else {}
                pid = str(submit.get("prompt_id") or "").strip()
        if not pid:
            return {"ok": False, "error": "missing_prompt_id", "job_key": job_key}
        out = sf.unqueue_to_pending(
            prompt_id=pid,
            server=server,
            data_root=root,
            job_key=job_key,
            job_path=job_path,
        )
        out["control"] = control
        _append_control_event(
            data_root=root,
            action=act,
            job_key=job_key or str(out.get("job_key") or "").strip() or None,
            job_path=job_path,
            actor=actor,
            source_surface=source_surface,
            reason=reason,
            ok=bool(out.get("ok")),
        )
        return out
    return {"ok": False, "error": "unknown_action", "detail": act}


def create_generate_job(
    *,
    shape: Path,
    pools: Path,
    data_root: Path,
    workflow_dir: Path,
    job_dir: Path,
    pick: str = "zip",
    limit: int = 1,
    picks_json: Optional[Path] = None,
    binds_override: Optional[Path] = None,
    pick_index: int = 0,
    job_suffix: str = "",
    output_prefix_root: Optional[str] = None,
    job_key_prefix: Optional[str] = None,
    dev: bool = False,
    dev_tuning: Optional[str] = None,
    dev_frames: Optional[int] = None,
    dev_steps: Optional[int] = None,
) -> dict[str, Any]:
    """
    Create job metadata/workflow without queue submission.

    This wraps ``shape_factory.cmd_generate`` so hourly and manual surfaces share
    the same creation semantics.
    """
    import shape_factory as sf

    args = argparse.Namespace(
        shape=str(shape),
        pools=str(pools),
        data_root=str(data_root),
        workflow_dir=str(workflow_dir),
        job_dir=str(job_dir),
        pick=str(pick),
        limit=int(limit),
        picks_json=str(picks_json) if picks_json else None,
        binds_override=str(binds_override) if binds_override else None,
        pick_index=int(pick_index),
        job_suffix=str(job_suffix or ""),
        output_prefix_root=str(output_prefix_root or "") or None,
        job_key_prefix=str(job_key_prefix or "") or None,
        dev=bool(dev),
        dev_tuning=dev_tuning,
        dev_frames=dev_frames,
        dev_steps=dev_steps,
        ignore_quarantine=False,
        quarantine_path=sf.DEFAULT_QUARANTINE_PATH,
    )
    rc = sf.cmd_generate(args)
    return {"ok": rc == 0, "rc": rc}


def main() -> int:
    p = argparse.ArgumentParser(description="Shared creation-control façade")
    sub = p.add_subparsers(dest="cmd", required=True)

    cg = sub.add_parser("create-generate", help="Create job/workflow from shape+pools (+ optional picks-json)")
    cg.add_argument("--shape", required=True)
    cg.add_argument("--pools", required=True)
    cg.add_argument("--data-root", required=True)
    cg.add_argument("--workflow-dir", required=True)
    cg.add_argument("--job-dir", required=True)
    cg.add_argument("--pick", default="zip")
    cg.add_argument("--limit", type=int, default=1)
    cg.add_argument("--picks-json", default=None)
    cg.add_argument("--binds-override", default=None)
    cg.add_argument("--pick-index", type=int, default=0)
    cg.add_argument("--job-suffix", default="")
    cg.add_argument("--output-prefix-root", default=None)
    cg.add_argument("--job-key-prefix", default=None)
    cg.add_argument("--dev", action="store_true")
    cg.add_argument("--dev-tuning", default=None)
    cg.add_argument("--dev-frames", type=int, default=None)
    cg.add_argument("--dev-steps", type=int, default=None)

    args = p.parse_args()
    if args.cmd == "create-generate":
        out = create_generate_job(
            shape=Path(args.shape),
            pools=Path(args.pools),
            data_root=Path(args.data_root),
            workflow_dir=Path(args.workflow_dir),
            job_dir=Path(args.job_dir),
            pick=args.pick,
            limit=args.limit,
            picks_json=Path(args.picks_json) if args.picks_json else None,
            binds_override=Path(args.binds_override) if args.binds_override else None,
            pick_index=args.pick_index,
            job_suffix=args.job_suffix,
            output_prefix_root=args.output_prefix_root,
            job_key_prefix=args.job_key_prefix,
            dev=bool(args.dev),
            dev_tuning=args.dev_tuning,
            dev_frames=args.dev_frames,
            dev_steps=args.dev_steps,
        )
        return 0 if out.get("ok") else int(out.get("rc") or 1)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
