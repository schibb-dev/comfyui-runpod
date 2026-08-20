"""
Output path helpers: flatten nested Comfy filename_prefix values and scan stray writes.

ComfyUI's save root is already the output bind mount (/ComfyUI/output). Workflows that
prefix paths with ``output/og/...`` land at ``<bind>/output/og/...`` (legacy nest).
Canonical flat layout is ``<bind>/og/...``.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

LIBRARY_DIRS = ("og", "wip", "experiments", "_status")
MEDIA_EXTS = {".mp4", ".png", ".webm", ".jpg", ".jpeg", ".webp", ".gif", ".xmp"}

OUTPUT_PREFIX_NODE_TYPES = frozenset(
    {
        "VHS_VideoCombine",
        "SaveImage",
        "SaveAnimatedWEBP",
        "SaveAnimatedPNG",
    }
)

# Relative roots under each output bind that indicate nested / wrong layout.
STRAY_REL_ROOTS = tuple(f"output/{name}" for name in LIBRARY_DIRS) + (
    "output/output/og",
    "output/output/wip",
    "output/output/experiments",
    "output/output/_status",
)

_WIN_ABS_PATH_RE = re.compile(r"^[a-zA-Z]:[\\/]")
_WIN_UNC_PATH_RE = re.compile(r"^\\\\")
_DATE_TOKEN_RE = re.compile(r"%date:([^%]+)%")
_LIB_DATE_DIR_RE = re.compile(
    r"^(?P<head>(?:og|wip|experiments))/\d{4}-\d{2}-\d{2}(?=/|$)"
)
_DATE_FMT_ALIASES = {
    "yyyy-MM-dd": "%Y-%m-%d",
    "yyyyMMdd": "%Y%m%d",
    "HHmmss": "%H%M%S",
    "hhmmss": "%H%M%S",
}


def flatten_output_prefix(value: str) -> str:
    """Strip redundant ``output/`` segments before library top-level dirs."""
    s = str(value or "").replace("\\", "/").strip()
    if not s or s.startswith("/") or _WIN_ABS_PATH_RE.match(s) or _WIN_UNC_PATH_RE.match(s):
        return s
    while "output/output/" in s:
        s = s.replace("output/output/", "output/", 1)
    for lib in LIBRARY_DIRS:
        if s == f"output/{lib}":
            return lib
        prefix = f"output/{lib}/"
        if s.startswith(prefix):
            return s[len("output/") :]
    return s


def expand_date_tokens(value: str, *, now: Optional[datetime] = None) -> str:
    """Expand Comfy ``%date:yyyy-MM-dd%`` tokens. Uses local time, like Comfy/VHS."""
    when = now if now is not None else datetime.now()

    def repl(match: re.Match[str]) -> str:
        token = match.group(1)
        fmt = _DATE_FMT_ALIASES.get(token, token)
        try:
            return when.strftime(fmt)
        except ValueError:
            return match.group(0)

    return _DATE_TOKEN_RE.sub(repl, str(value or ""))


def apply_queue_date_to_prefix(value: str, *, now: Optional[datetime] = None) -> str:
    """Stamp library date folders to the Comfy queue/submit day.

    Expands leftover ``%date%`` tokens, then rewrites ``og/YYYY-MM-DD`` (also wip/
    experiments) so a restored prompt does not keep writing into the generate-day
    folder.
    """
    when = now if now is not None else datetime.now()
    s = flatten_output_prefix(expand_date_tokens(value, now=when))
    today = when.strftime("%Y-%m-%d")
    return _LIB_DATE_DIR_RE.sub(lambda m: f"{m.group('head')}/{today}", s, count=1)


def normalize_prompt_output_prefixes(prompt_obj: Dict[str, Any]) -> List[str]:
    """Rewrite ``filename_prefix`` inputs in-place; return human-readable change lines."""
    changes: List[str] = []
    for nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        raw = inputs.get("filename_prefix")
        if not isinstance(raw, str):
            continue
        new = flatten_output_prefix(raw)
        if new != raw:
            inputs["filename_prefix"] = new
            changes.append(f"{nid}.filename_prefix: {raw!r} -> {new!r}")
    return changes


def apply_queue_date_to_prompt(
    prompt_obj: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> List[str]:
    """Stamp ``filename_prefix`` library dates to the Comfy queue day."""
    changes: List[str] = []
    when = now if now is not None else datetime.now()
    for nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        raw = inputs.get("filename_prefix")
        if not isinstance(raw, str):
            continue
        new = apply_queue_date_to_prefix(raw, now=when)
        if new != raw:
            inputs["filename_prefix"] = new
            changes.append(f"{nid}.filename_prefix: {raw!r} -> {new!r}")
    return changes


def _flatten_widget_value(value: str) -> tuple[str, bool]:
    new = flatten_output_prefix(value)
    return new, new != value


def normalize_ui_workflow_output_prefixes(workflow: dict[str, Any]) -> list[str]:
    """Flatten nested library prefixes in LiteGraph workflow save-node widgets."""
    changes: list[str] = []
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_type = str(node.get("type") or node.get("class_type") or "")
        if node_type not in OUTPUT_PREFIX_NODE_TYPES:
            continue
        nid = node.get("id")
        widgets = node.get("widgets_values")
        if isinstance(widgets, dict):
            raw = widgets.get("filename_prefix")
            if isinstance(raw, str):
                new, changed = _flatten_widget_value(raw)
                if changed:
                    widgets["filename_prefix"] = new
                    changes.append(f"{nid}.{node_type}.filename_prefix: {raw!r} -> {new!r}")
        elif isinstance(widgets, list):
            for idx, raw in enumerate(widgets):
                if not isinstance(raw, str):
                    continue
                new, changed = _flatten_widget_value(raw)
                if changed:
                    widgets[idx] = new
                    changes.append(f"{nid}.{node_type}.widgets_values[{idx}]: {raw!r} -> {new!r}")
    return changes


def read_bind_output_dir(repo_root: Optional[Path] = None) -> Optional[Path]:
    """Resolve COMFYUI_BIND_OUTPUT_DIR from env or repo ``.env``."""
    raw = os.environ.get("COMFYUI_BIND_OUTPUT_DIR", "").strip()
    if not raw and repo_root is not None:
        env_path = repo_root / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("COMFYUI_BIND_OUTPUT_DIR="):
                    raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def stray_scan_roots(
    repo_root: Path,
    *,
    canonical_output: Optional[Path] = None,
) -> List[Tuple[str, Path]]:
    """Return labeled directories that may hold nested / legacy output writes."""
    repo_root = repo_root.resolve()
    canonical = canonical_output or read_bind_output_dir(repo_root)
    roots: List[Tuple[str, Path]] = []

    if canonical is not None:
        canonical = canonical.resolve()
        for rel in STRAY_REL_ROOTS:
            p = (canonical / rel).resolve()
            if p.is_dir():
                roots.append((f"nested:{rel}", p))

    repo_ws_out = (repo_root / "workspace" / "output").resolve()
    if repo_ws_out.is_dir():
        roots.append(("repo:workspace/output", repo_ws_out))
        for name in LIBRARY_DIRS:
            p = repo_ws_out / name
            if p.is_dir():
                roots.append((f"repo:workspace/output/{name}", p))
        nested = repo_ws_out / "output"
        if nested.is_dir():
            roots.append(("repo:workspace/output/output", nested))

    shadow = Path("/mnt/e/comfyui-runpod-shadow/workspace/output")
    if shadow.is_dir():
        roots.append(("shadow:e/comfyui-runpod-shadow/workspace/output", shadow))

    # Deduplicate by resolved path, keep first label.
    seen: set[str] = set()
    out: List[Tuple[str, Path]] = []
    for label, path in roots:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append((label, path))
    return out


@dataclass
class StrayFile:
    root_label: str
    relpath: str
    size: int
    mtime_iso: str


@dataclass
class StrayScanReport:
    scanned_at: str
    since_hours: float
    canonical_output: Optional[str]
    roots_scanned: List[str] = field(default_factory=list)
    files: List[StrayFile] = field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTS


def scan_stray_outputs(
    repo_root: Path,
    *,
    since_hours: float = 48.0,
    canonical_output: Optional[Path] = None,
    max_files: int = 200,
) -> StrayScanReport:
    repo_root = repo_root.resolve()
    canonical = canonical_output or read_bind_output_dir(repo_root)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0.0, since_hours))
    cutoff_ts = cutoff.timestamp()

    report = StrayScanReport(
        scanned_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        since_hours=since_hours,
        canonical_output=str(canonical) if canonical else None,
    )

    for label, root in stray_scan_roots(repo_root, canonical_output=canonical):
        report.roots_scanned.append(f"{label} -> {root}")
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or not _is_media(path):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff_ts:
                continue
            rel = str(path.relative_to(root))
            report.files.append(
                StrayFile(
                    root_label=label,
                    relpath=rel,
                    size=int(st.st_size),
                    mtime_iso=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                )
            )

    report.files.sort(key=lambda f: f.mtime_iso, reverse=True)
    if len(report.files) > max_files:
        report.files = report.files[:max_files]
    report.total_files = len(report.files)
    report.total_bytes = sum(f.size for f in report.files)
    return report


def bind_output_guard_messages(repo_root: Path) -> List[str]:
    """Warnings/errors for COMFYUI_BIND_OUTPUT_DIR misconfiguration."""
    repo_root = repo_root.resolve()
    messages: List[str] = []
    env_path = repo_root / ".env"
    raw = os.environ.get("COMFYUI_BIND_OUTPUT_DIR", "").strip()

    if not raw and env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("COMFYUI_BIND_OUTPUT_DIR="):
                raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    if not raw:
        messages.append(
            "ERROR: COMFYUI_BIND_OUTPUT_DIR unset — docker-compose defaults to ./workspace/output (repo trap)"
        )
        return messages

    if raw.startswith("./") or raw.startswith("workspace/"):
        messages.append(f"ERROR: COMFYUI_BIND_OUTPUT_DIR is repo-relative: {raw}")

    bind = Path(raw).expanduser()
    try:
        bind_resolved = bind.resolve()
    except OSError:
        bind_resolved = bind

    repo_trap = (repo_root / "workspace" / "output").resolve()
    if bind_resolved == repo_trap:
        messages.append(
            f"ERROR: COMFYUI_BIND_OUTPUT_DIR points at repo workspace/output: {bind_resolved}"
        )

    lowered = str(bind_resolved).lower()
    if "comfyui-runpod-shadow" in lowered:
        messages.append(f"WARN: COMFYUI_BIND_OUTPUT_DIR still on E: shadow tree: {bind_resolved}")

    if not bind.is_dir():
        messages.append(f"WARN: COMFYUI_BIND_OUTPUT_DIR is not a directory: {bind}")

    return messages
