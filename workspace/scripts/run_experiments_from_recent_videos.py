#!/usr/bin/env python3
"""
Find MP4/PNG files (recent or matching a string) and run experiments (generate + run).

By default uses each input video's embedded seed so experiments match the original.
Use --no-seed-from-video to use --seed for all instead.

Usage:
  python run_experiments_from_recent_videos.py [--video-dir DIR] [--out-root DIR] [--limit N] [--match STR] [--dry-run]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Run experiments from videos created in the last 24 hours")
    ap.add_argument(
        "--video-dir",
        default="",
        help="Directory to search for MP4s (default: workspace/output or workspace/output/output)",
    )
    ap.add_argument(
        "--out-root",
        default="",
        help="Experiment output root (default: workspace/output/output/experiments)",
    )
    ap.add_argument("--limit", type=int, default=3, help="Number of experiments to run (default: 3)")
    ap.add_argument(
        "--seed",
        type=int,
        default=12345,
        help="Seed for generate when not read from video (default: 12345). Default is to use each video's embedded seed.",
    )
    ap.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL for run")
    ap.add_argument("--dry-run", action="store_true", help="Only list videos that would be used")
    ap.add_argument(
        "--no-seed-from-video",
        action="store_true",
        help="Do not extract seed from video; use --seed for all experiments.",
    )
    ap.add_argument(
        "--match",
        default="",
        help="Only use videos whose path/name contains this string (e.g. 133216). Ignores 24h cutoff when set.",
    )
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent
    repo = script_dir.parent.parent
    workspace = repo / "workspace"

    if args.video_dir:
        video_dir = Path(args.video_dir)
    else:
        video_dir = workspace / "output" / "output"
        if not video_dir.exists():
            video_dir = workspace / "output"
    if not video_dir.exists():
        print("Video directory not found:", video_dir, file=sys.stderr)
        return 1

    out_root = Path(args.out_root) if args.out_root else (workspace / "output" / "output" / "experiments")
    if not out_root.is_absolute():
        out_root = workspace / "output" / "output" / "experiments"

    cutoff = time.time() - (24 * 3600)
    match_substring = (args.match or "").strip()
    # Exclude paths under former experiment dirs (e.g. .../experiments/tune_xxx/...).
    def under_experiments(p: Path) -> bool:
        try:
            parts = p.resolve().parts
        except Exception:
            return False
        if "experiments" not in parts:
            return False
        i = parts.index("experiments")
        return i + 1 < len(parts)

    media: list[tuple[float, Path]] = []
    for ext in ("*.mp4", "*.png"):
        for f in video_dir.rglob(ext):
            if under_experiments(f):
                continue
            if match_substring and match_substring not in str(f):
                continue
            try:
                mtime = f.stat().st_mtime
                if not match_substring and mtime < cutoff:
                    continue
                media.append((mtime, f))
            except OSError:
                continue

    media.sort(key=lambda x: -x[0])
    chosen = [p for _, p in media[: args.limit]]

    if not chosen:
        msg = "No MP4/PNG files (excluding former experiments)"
        if match_substring:
            msg += f" matching {match_substring!r}"
        else:
            msg += " modified in the last 24 hours"
        print(msg, "under", video_dir, file=sys.stderr)
        return 1

    print("Media to use (newest first):")
    for p in chosen:
        print(" ", p)
    if args.dry_run:
        print("Dry run: not generating or running.")
        return 0

    tune_script = script_dir / "tune_experiment.py"
    if not tune_script.exists():
        print("tune_experiment.py not found at", tune_script, file=sys.stderr)
        return 1

    for i, base_media in enumerate(chosen):
        print("\n--- Experiment", i + 1, "of", len(chosen), ":", base_media.name, "---")
        seed = args.seed
        if not args.no_seed_from_video:
            seed_result = subprocess.run(
                [sys.executable, str(tune_script), "seed-from-media", str(base_media.resolve())],
                cwd=str(workspace),
                capture_output=True,
                text=True,
            )
            if seed_result.returncode == 0 and seed_result.stdout:
                try:
                    seed = int(seed_result.stdout.strip().splitlines()[0].strip())
                    print("Using seed from input video:", seed)
                except ValueError:
                    pass
        gen_cmd = [
            sys.executable,
            str(tune_script),
            "generate",
            str(base_media.resolve()),
            "--out-root",
            str(out_root.resolve()),
            "--seed",
            str(seed),
            "--defaults",
            "core",
            "--max-runs",
            "50",
        ]
        print("Generate:", " ".join(gen_cmd))
        result = subprocess.run(gen_cmd, cwd=str(workspace), capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr or "Generate failed", file=sys.stderr)
            continue
        exp_dir = (result.stdout or "").strip().splitlines()[-1].strip() if result.stdout else ""
        if not exp_dir or not Path(exp_dir).exists():
            from datetime import datetime
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            exp_id = f"tune_{base_media.stem}_{stamp}"
            exp_dir = str(out_root / exp_id)
        print("Experiment dir:", exp_dir)
        run_cmd = [
            sys.executable,
            str(tune_script),
            "run",
            exp_dir,
            "--server",
            args.server,
            "--submit-all",
        ]
        print("Run:", " ".join(run_cmd))
        subprocess.run(run_cmd, cwd=str(workspace))

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
