#!/usr/bin/env python3
"""
Locate + recover missing source assets (content-addressed).

Powers the Factory-map "Recover source" action. Recovery ladder (see
`.cursor/rules/asset-recovery.mdc`):
  1. already present in input/
  2. exact basename under a search root (input/, /mnt/e/unsorted, ...)
  3. verified remote fetch by sha256 (https://aigc.uploads.dev/image/<sha>.jpeg)
  4. hash-token walk of search roots (lazy; last resort)

Every placement into input/ is integrity-checked when the filename embeds a
sha256 (``sha256(bytes) == name`` gate), then registered in the asset registry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from http_retry import urlopen_read_with_retry

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

REMOTE_IMAGE_URL = "https://aigc.uploads.dev/image/{sha}.jpeg"
_SHA_RE = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
_DEFAULT_EXTRA_ROOTS = ("/mnt/e/unsorted",)


def sha_from_name(name: str) -> Optional[str]:
    m = _SHA_RE.search(Path(str(name or "")).name)
    return m.group(0).lower() if m else None


def _search_roots(
    workspace_root: Optional[Path], extra_roots: Optional[List[Path]] = None
) -> List[Path]:
    roots: List[Path] = []
    if workspace_root is not None:
        roots.append(Path(workspace_root) / "input")
    for extra in extra_roots or [Path(p) for p in _DEFAULT_EXTRA_ROOTS]:
        roots.append(Path(extra))
    seen: set[str] = set()
    out: List[Path] = []
    for r in roots:
        try:
            if r.is_dir() and str(r) not in seen:
                seen.add(str(r))
                out.append(r)
        except OSError:
            continue
    return out


def _http_get(url: str, *, timeout: int = 45) -> bytes:
    return urlopen_read_with_retry(
        method="GET",
        url=url,
        headers={"User-Agent": "comfyui-runpod-recover/1"},
        timeout_s=timeout,
        retry_attempts=3,
        retry_backoff_s=0.35,
    )


def _verify_sha(data: bytes, sha: Optional[str]) -> bool:
    return sha is None or hashlib.sha256(data).hexdigest() == sha


def _atomic_write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)


def _build_walk_index(roots: List[Path]) -> Tuple[Dict[str, Path], Dict[str, Path]]:
    by_name: Dict[str, Path] = {}
    by_sha: Dict[str, Path] = {}
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = Path(dirpath) / f
                by_name.setdefault(f, p)
                s = sha_from_name(f)
                if s:
                    by_sha.setdefault(s, p)
    return by_name, by_sha


def recover_one(
    name: str,
    *,
    input_dir: Path,
    search_roots: List[Path],
    allow_remote: bool = True,
    allow_local: bool = True,
    walk_index: Optional[Tuple[Dict[str, Path], Dict[str, Path]]] = None,
) -> Dict[str, Any]:
    bn = Path(str(name or "")).name
    if not bn:
        return {"name": name, "ok": False, "error": "bad_name"}
    dest = input_dir / bn
    if dest.is_file():
        return {"name": bn, "ok": True, "method": "present", "relpath": f"input/{bn}"}
    sha = sha_from_name(bn)

    # (2) exact basename directly under a search root — cheap, no walk.
    if allow_local:
        for root in search_roots:
            cand = root / bn
            if cand.is_file():
                data = cand.read_bytes()
                if _verify_sha(data, sha):
                    _atomic_write(dest, data)
                    return {"name": bn, "ok": True, "method": "local", "source": str(cand), "relpath": f"input/{bn}"}

    # (3) verified remote fetch by sha.
    if allow_remote and sha:
        try:
            data = _http_get(REMOTE_IMAGE_URL.format(sha=sha))
        except Exception as e:  # noqa: BLE001
            data = None
            remote_err = f"http:{type(e).__name__}"
        else:
            remote_err = ""
        if data is not None:
            if not _verify_sha(data, sha):
                return {"name": bn, "ok": False, "method": "remote", "error": "sha_mismatch"}
            _atomic_write(dest, data)
            return {"name": bn, "ok": True, "method": "remote", "relpath": f"input/{bn}"}
    else:
        remote_err = "no_sha" if allow_remote else "remote_disabled"

    # (4) hash-token walk (lazy; last resort).
    if allow_local and walk_index is not None:
        by_name, by_sha = walk_index
        hit = by_name.get(bn) or (by_sha.get(sha) if sha else None)
        if hit and hit.is_file():
            data = hit.read_bytes()
            if _verify_sha(data, sha):
                _atomic_write(dest, data)
                return {"name": bn, "ok": True, "method": "walk", "source": str(hit), "relpath": f"input/{bn}"}

    return {"name": bn, "ok": False, "method": "none", "error": remote_err or "not_found"}


def recover_names(
    names: List[str],
    *,
    workspace_root: Path,
    allow_remote: bool = True,
    allow_local: bool = True,
    registry_path: Optional[Path] = None,
    extra_roots: Optional[List[Path]] = None,
    deep_walk: bool = True,
) -> Dict[str, Any]:
    workspace_root = Path(workspace_root)
    input_dir = workspace_root / "input"
    roots = _search_roots(workspace_root, extra_roots)

    # Build the walk index once per batch, only if some name isn't trivially
    # resolvable (present / direct / remote-by-sha). Cheap cases skip the walk.
    def _trivial(n: str) -> bool:
        bn = Path(n).name
        if (input_dir / bn).is_file():
            return True
        for root in roots:
            if (root / bn).is_file():
                return True
        return bool(allow_remote and sha_from_name(bn))

    walk_index: Optional[Tuple[Dict[str, Path], Dict[str, Path]]] = None
    if allow_local and deep_walk and any(not _trivial(n) for n in names):
        walk_index = _build_walk_index(roots)

    con = None
    if registry_path is not None:
        try:
            import asset_registry as areg  # type: ignore

            con = areg.connect(Path(registry_path))
        except Exception:  # noqa: BLE001
            con = None

    results: List[Dict[str, Any]] = []
    for n in names:
        r = recover_one(
            n,
            input_dir=input_dir,
            search_roots=roots,
            allow_remote=allow_remote,
            allow_local=allow_local,
            walk_index=walk_index,
        )
        if r.get("ok") and con is not None:
            try:
                import asset_registry as areg  # type: ignore

                r["content_id"] = areg.register(
                    con, input_dir / Path(n).name, relpath=r["relpath"], refs=["recover"]
                )
            except Exception:  # noqa: BLE001
                pass
        results.append(r)

    if con is not None:
        try:
            con.close()
        except Exception:  # noqa: BLE001
            pass

    recovered = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "recovered": recovered, "total": len(results), "results": results}


def audit_family_missing_sources(
    *,
    data_root: Path,
    workspace_root: Path,
    family: str,
) -> Dict[str, Any]:
    """Scan a family's jobs for load_image source bindings whose file is missing."""
    jobs_dir = Path(data_root) / "shape_factory" / "jobs" / str(family)
    input_dir = Path(workspace_root) / "input"
    scanned = 0
    by_name: Dict[str, Dict[str, Any]] = {}
    if jobs_dir.is_dir():
        for jp in sorted(jobs_dir.glob("*.job.json")):
            try:
                jd = json.loads(jp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            bindings = jd.get("bindings") if isinstance(jd.get("bindings"), dict) else {}
            for slot, row in bindings.items():
                if not isinstance(row, dict) or row.get("binding_type") != "load_image":
                    continue
                scanned += 1
                path = str(row.get("path") or "")
                if not path:
                    continue
                bn = Path(path).name
                if (input_dir / bn).is_file() or Path(path).is_file():
                    continue
                by_name.setdefault(
                    bn,
                    {
                        "basename": bn,
                        "sha": sha_from_name(bn),
                        "slot": slot,
                        "job_key": jd.get("job_key"),
                        "output": (jd.get("outputs") or [None])[0],
                    },
                )
    missing = sorted(by_name.values(), key=lambda m: m["basename"])
    return {
        "ok": True,
        "family": str(family),
        "scanned": scanned,
        "missing_count": len(missing),
        "missing": missing,
    }
