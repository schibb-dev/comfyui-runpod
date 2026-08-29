#!/usr/bin/env python3
"""
Copy stills from a Windows-writable NTFS inbox into the Comfy bind input dir.

Explorer writes to ``\\\\wsl.localhost\\...\\comfyui-runpod-data\\input`` are flaky
(huge ext4 directory + 9p). Drop files on ``E:\\comfyui-runpod-inbox`` instead;
hourly (or this CLI) copies them into ``COMFYUI_BIND_INPUT_DIR``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STILL_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
DEFAULT_INBOX = Path("/mnt/e/comfyui-runpod-inbox")


def default_input_root() -> Path:
    env = os.environ.get("COMFYUI_BIND_INPUT_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path("/home/yuji/comfyui-runpod-data/input").resolve()


def default_inbox() -> Path:
    env = os.environ.get("WINDOWS_INPUT_INBOX", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_INBOX


def _is_still(name: str) -> bool:
    lower = str(name or "").lower()
    if not lower or lower.startswith("."):
        return False
    if ":zone.identifier" in lower:
        return False
    return Path(lower).suffix in STILL_EXTS


def ingest_windows_input_inbox(
    *,
    inbox: Path,
    dest: Path,
    apply: bool = False,
    now_ts: Optional[float] = None,
) -> Dict[str, Any]:
    inbox = inbox.expanduser().resolve()
    dest = dest.expanduser().resolve()
    now = float(now_ts) if now_ts is not None else time.time()
    day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
    ingested_dir = inbox / "_ingested" / day
    stats: Dict[str, Any] = {
        "ok": False,
        "inbox": str(inbox),
        "dest": str(dest),
        "apply": bool(apply),
        "copied": 0,
        "skipped_exists": 0,
        "skipped_not_still": 0,
        "moved": 0,
        "files": [],
    }
    if not inbox.is_dir():
        stats["error"] = "inbox_missing"
        return stats
    if not dest.is_dir():
        stats["error"] = "dest_missing"
        return stats

    try:
        from input_still_catalog import strip_download_copy_suffix  # type: ignore
    except Exception:  # pragma: no cover

        def strip_download_copy_suffix(n: str) -> str:  # type: ignore
            return n

    copied: List[str] = []
    for ent in sorted(inbox.iterdir(), key=lambda p: p.name.lower()):
        if not ent.is_file():
            continue
        if not _is_still(ent.name):
            stats["skipped_not_still"] += 1
            continue
        # Normalize Windows `` (1)`` / `` (2)`` re-download names to the canonical basename.
        dest_name = strip_download_copy_suffix(ent.name) or ent.name
        target = dest / dest_name
        rec: Dict[str, Any] = {"name": ent.name, "src": str(ent), "dest_name": dest_name}
        if target.exists():
            stats["skipped_exists"] += 1
            rec["action"] = "skip_exists"
            if apply:
                ingested_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(ent), str(ingested_dir / ent.name))
                stats["moved"] += 1
                rec["ingested"] = str(ingested_dir / ent.name)
        else:
            rec["action"] = "copy"
            rec["dest"] = str(target)
            if apply:
                shutil.copy2(str(ent), str(target))
                ingested_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(ent), str(ingested_dir / ent.name))
                stats["copied"] += 1
                stats["moved"] += 1
                rec["ingested"] = str(ingested_dir / ent.name)
            else:
                stats["copied"] += 1
        copied.append(dest_name)
        stats["files"].append(rec)

    stats["ok"] = True
    stats["pending_names"] = copied
    return stats


def ensure_inbox(inbox: Path) -> None:
    inbox.mkdir(parents=True, exist_ok=True)
    readme = inbox / "README.txt"
    if readme.is_file():
        return
    readme.write_text(
        "Drop stills here (png / jpeg / webp).\r\n"
        "Hourly copies them into the Comfy input folder on the Linux disk,\r\n"
        "then moves the originals into _ingested\\YYYY-MM-DD\\.\r\n"
        "Do not save into workspace\\input in the git clone — that is not Comfy's bind.\r\n",
        encoding="utf-8",
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Ingest Windows NTFS inbox into Comfy input/")
    p.add_argument("--inbox", type=Path, default=None)
    p.add_argument("--dest", type=Path, default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--ensure", action="store_true", help="Create inbox + README if missing")
    args = p.parse_args()
    inbox = (args.inbox or default_inbox()).expanduser()
    dest = (args.dest or default_input_root()).expanduser()
    if args.ensure:
        ensure_inbox(inbox)
    out = ingest_windows_input_inbox(inbox=inbox, dest=dest, apply=bool(args.apply))
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
