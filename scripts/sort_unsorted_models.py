#!/usr/bin/env python3
"""
Sort loose model files under a source tree (e.g. E:\\unsorted) into ComfyUI-style folders
under --dest (e.g. E:\\models), and optionally delete duplicate files.

By default, files **>= --size-only-above-mb** are treated as duplicates when byte sizes match (no hashing).
Smaller files use SHA256 within same-size groups to reduce false duplicate deletes.
Use `--hash-dedupe` to SHA256 everything (slow).

Examples:
  python scripts/sort_unsorted_models.py --source E:/unsorted --dest E:/models
  python scripts/sort_unsorted_models.py --source E:/unsorted --dest E:/models --apply-moves --apply-deletes

Safety: default is dry-run (no writes). Deletes require --apply-deletes explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path


MODEL_SUFFIXES = {
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".onnx",
    ".gguf",
    ".safetensors.index.json",
}

# Order matters: first keyword tuple match wins (substring search on full path lowercased).
KEYWORD_RULES: list[tuple[tuple[str, ...], str]] = [
    (("controlnet", "control_v11", "control_v10", "/cn/", "\\cn\\"), "controlnet"),
    (("ipadapter", "ip_adapter", "ip-adapter"), "ipadapter"),
    (("animatediff", "motion_module", "temporald"), "animatediff_models"),
    (("clip_vision", "sigclip-vision", "clip-vision"), "clip_vision"),
    (("esrgan", "realesrgan", "swinir", "real-esrgan", "upscale", "4x_", "2x_", "1x_"), "upscale_models"),
    (("embedding", "embeddings", "textual"), "embeddings"),
    (("vae", "/vae/", "\\vae\\"), "vae"),
    (("lycoris", "loha", "lokr"), "loras"),
    (("/lora/", "\\lora\\", "lora_", "_lora", "-lora"), "loras"),
    (("t5xxl_fp16", "t5xxl", "clip_l", "clip_h", "clip-g"), "clip"),
]


def classify_model(path: Path) -> str:
    """Return ComfyUI models subfolder name (best effort)."""
    low = str(path).lower()
    name = path.name.lower()

    for keys, folder in KEYWORD_RULES:
        if any(k in low for k in keys):
            return folder

    if name.endswith(".gguf"):
        # Flux / LLM-style weights often land here
        return "unet"

    suf = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError:
        return "unknown"

    if suf in (".ckpt",):
        return "checkpoints"

    if suf == ".safetensors":
        if size >= 500 * 1024 * 1024:
            return "checkpoints"
        if size <= 220 * 1024 * 1024:
            return "loras"
        return "checkpoints"

    if suf == ".pt":
        if size < 50 * 1024 * 1024:
            return "embeddings"
        return "loras"

    if suf == ".pth":
        return "upscale_models"

    if suf == ".bin":
        return "clip"

    if suf == ".onnx":
        return "controlnet"

    return "unknown"


def sha256_file(path: Path, chunk: int = 16 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def iter_candidate_files(root: Path, min_bytes: int) -> list[Path]:
    out: list[Path] = []
    for dirpath, _, filenames in os.walk(root):
        dp = Path(dirpath)
        # Skip quarantine / metadata dirs from prior runs
        if any(p.startswith("_dup_quarantine") or p.startswith("_sort_") for p in dp.parts):
            continue
        for fn in filenames:
            p = dp / fn
            suf = p.suffix.lower()
            if suf == ".json" and not fn.endswith(".safetensors.index.json"):
                continue
            if suf not in MODEL_SUFFIXES and not fn.endswith(".safetensors.index.json"):
                continue
            try:
                if p.stat().st_size < min_bytes:
                    continue
            except OSError:
                continue
            out.append(p)
    return out


def choose_duplicate_keeper(paths: list[Path], dest_root: Path) -> Path:
    """Prefer a file already under dest_root; else shortest path name for stability."""

    def score(pp: Path) -> tuple[int, int, str]:
        try:
            rp = pp.resolve()
        except OSError:
            rp = pp
        under_dest = 0
        try:
            rp.relative_to(dest_root.resolve())
            under_dest = 1
        except ValueError:
            pass
        # Prefer under dest, then shorter path (often less nested junk), then alphabetic
        return (-under_dest, len(str(rp)), str(rp))

    return sorted(paths, key=score)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="Sort + dedupe model files from a messy folder tree.")
    ap.add_argument("--source", type=Path, default=Path("E:/unsorted"), help="Folder to scan (default: E:/unsorted)")
    ap.add_argument("--dest", type=Path, default=Path("E:/models"), help="ComfyUI models root (default: E:/models)")
    ap.add_argument("--min-bytes", type=int, default=1024 * 1024, help="Skip files smaller than this (default 1 MiB)")
    ap.add_argument("--apply-moves", action="store_true", help="Actually move files into dest/<category>/")
    ap.add_argument("--apply-deletes", action="store_true", help="Delete duplicate files (keepers kept)")
    ap.add_argument(
        "--size-only-above-mb",
        type=int,
        default=64,
        metavar="N",
        help="For files >= N MiB, same size => duplicate (no hash). Below N, hash within size groups (default: 64).",
    )
    ap.add_argument(
        "--hash-dedupe",
        action="store_true",
        help="SHA256 all duplicate groups regardless of size (slowest, strongest).",
    )
    ap.add_argument("--report", type=Path, default=None, help="Write TSV report path")
    args = ap.parse_args()

    src: Path = args.source
    dest: Path = args.dest

    if not src.is_dir():
        print(f"ERROR: source not a directory: {src}", file=sys.stderr)
        return 1

    dry_run = not (args.apply_moves or args.apply_deletes)
    if args.apply_deletes and not args.apply_moves:
        print("NOTE: --apply-deletes without --apply-moves only removes duplicate paths inside source (see plan).")

    thr_bytes = max(0, args.size_only_above_mb) * 1024 * 1024
    if args.hash_dedupe:
        mode = "SHA256 (all)"
    else:
        mode = f"size-only >= {args.size_only_above_mb} MiB, hash below"
    print(f"Scanning {src} ... (dedupe: {mode})", flush=True)
    files = iter_candidate_files(src, args.min_bytes)
    print(f"Found {len(files)} candidate model files (suffix filter, min size {args.min_bytes}).", flush=True)

    # --- Duplicate detection: default same-size groups; optional hash split within size
    by_size: dict[int, list[Path]] = defaultdict(list)
    for p in files:
        try:
            by_size[p.stat().st_size].append(p)
        except OSError:
            continue

    dup_groups: dict[str, list[Path]] = {}
    for sz, plist in by_size.items():
        if len(plist) < 2:
            continue
        if args.hash_dedupe or sz < thr_bytes:
            by_hash: dict[str, list[Path]] = defaultdict(list)
            for p in plist:
                try:
                    digest = sha256_file(p)
                except OSError as e:
                    print(f"WARN: could not hash {p}: {e}", file=sys.stderr)
                    continue
                by_hash[digest].append(p)
            for digest, paths in by_hash.items():
                if len(paths) > 1:
                    dup_groups[digest] = paths
        else:
            dup_groups[f"size:{sz}"] = plist

    print(f"Duplicate groups: {len(dup_groups)}", flush=True)
    delete_targets: list[Path] = []
    for key, paths in sorted(dup_groups.items(), key=lambda x: -len(x[1])):
        keeper = choose_duplicate_keeper(paths, dest)
        losers = [p for p in paths if p != keeper]
        delete_targets.extend(losers)
        sz = paths[0].stat().st_size
        label = key[:24] + "…" if len(key) > 24 else key
        print(f"  DUP {label} size={sz} keep={keeper}", flush=True)
        for l in losers:
            print(f"      drop={l}", flush=True)

    print(f"\nPlanned duplicate deletions: {len(delete_targets)}", flush=True)

    # --- Classification moves (skip paths that are duplicate losers)
    skip_from_hash_dups = set(delete_targets)
    moves: list[tuple[Path, Path, str]] = []
    delete_identical_to_dest: list[Path] = []

    for p in files:
        if p in skip_from_hash_dups:
            continue
        cat = classify_model(p)
        target_dir = dest / cat
        target = target_dir / p.name
        if target.exists():
            try:
                same_size = target.stat().st_size == p.stat().st_size
            except OSError:
                same_size = False
            if same_size:
                if args.hash_dedupe:
                    try:
                        if sha256_file(p) == sha256_file(target):
                            delete_identical_to_dest.append(p)
                            continue
                    except OSError:
                        pass
                else:
                    big = p.stat().st_size >= thr_bytes
                    if big or thr_bytes <= 0:
                        delete_identical_to_dest.append(p)
                        continue
                    try:
                        if sha256_file(p) == sha256_file(target):
                            delete_identical_to_dest.append(p)
                            continue
                    except OSError:
                        pass
                # same name+size but not redundant (or different hash on small file): pick alt name
            stem, suf = p.stem, p.suffix
            for i in range(1, 50):
                alt = target_dir / f"{stem}_unsorted{i}{suf}"
                if not alt.exists():
                    target = alt
                    break
        moves.append((p, target, cat))

    print(f"\nPlanned moves (into {dest}/<category>/): {len(moves)}")
    for frm, to, cat in moves[:80]:
        print(f"  [{cat}] {frm.name} -> {to}")
    if len(moves) > 80:
        print(f"  … {len(moves) - 80} more")

    if args.hash_dedupe:
        dest_note = "same name+size+hash"
    elif thr_bytes <= 0:
        dest_note = "same name+size"
    else:
        dest_note = f"large files same name+size only; smaller hashed (threshold {args.size_only_above_mb} MiB)"
    print(f"\nSources redundant vs existing dest ({dest_note}): {len(delete_identical_to_dest)}")
    for p in delete_identical_to_dest[:40]:
        print(f"  delete source (already in models): {p}")
    if len(delete_identical_to_dest) > 40:
        print(f"  … {len(delete_identical_to_dest) - 40} more")

    if args.report:
        lines = ["action\tfrom\tto\textra"]
        for key, paths in dup_groups.items():
            keeper = choose_duplicate_keeper(paths, dest)
            for p in paths:
                act = "keep" if p == keeper else "delete_dup"
                lines.append(f"{act}\t{p}\t{keeper}\t{key}")
        for p in delete_identical_to_dest:
            lines.append(f"delete_identical_dest\t{p}\t\talready_in_models")
        for frm, to, cat in moves:
            lines.append(f"move\t{frm}\t{to}\t{cat}")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nWrote report {args.report}")

    if dry_run:
        print("\n*** DRY RUN — no files moved or deleted. ***")
        print("    --apply-deletes  -> remove duplicates (see 'drop=' list)")
        print("    --apply-moves    -> move files + delete sources already identical at dest")
        print("    Add --hash-dedupe for SHA256 (slow) instead of size-only matching.")
        return 0

    if args.apply_deletes:
        for p in delete_targets:
            if not p.exists():
                print(f"skip missing (already gone): {p}", flush=True)
                continue
            try:
                p.unlink()
                print(f"deleted duplicate {p}", flush=True)
            except OSError as e:
                print(f"ERROR deleting {p}: {e}", file=sys.stderr)

    if args.apply_moves:
        for p in delete_identical_to_dest:
            if not p.exists():
                print(f"skip missing (already gone): {p}", flush=True)
                continue
            try:
                p.unlink()
                print(f"deleted (already in dest) {p}", flush=True)
            except OSError as e:
                print(f"ERROR deleting redundant source {p}: {e}", file=sys.stderr)
        for frm, to, cat in moves:
            if not frm.exists():
                print(f"skip missing (no move): {frm}", flush=True)
                continue
            to.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(frm), str(to))
                print(f"moved -> {to}")
            except OSError as e:
                print(f"ERROR move {frm} -> {to}: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
