#!/usr/bin/env python3
"""
Sanity-check that the extracted ComfyUI run metadata agrees across:
- companion PNG
- _OG_ MP4
- _UPIN_ MP4

We compare stable hashes derived from embedded metadata:
- used_seed (heuristic; RandomNoise.noise_seed preferred)
- prompt_sha256
- workflow_sha256 (if present)
- preset_sha256 (derived from prompt)

Prints a mismatch report and exits non-zero if any mismatch is found.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from comfy_meta_lib import (
    collect_seeds_from_prompt,
    extract_preset,
    extract_prompt_workflow_from_png_chunks,
    extract_prompt_workflow_from_tags,
    ffprobe_format_tags,
    read_png_text_chunks,
    stable_json_sha256,
)


_VARIANT_RE = re.compile(r"_(OG|UPIN)_(\d+)$", re.IGNORECASE)


def _group_key(stem: str) -> str:
    # Strip trailing _OG_00001 / _UPIN_00001 if present.
    return _VARIANT_RE.sub("", stem)


def _variant(stem: str, suffix: str) -> str:
    # In your output convention the companion PNG is typically named like:
    #   ..._OG_00001.png
    # so we must classify by extension first (otherwise we'd call it "OG").
    m = _VARIANT_RE.search(stem)
    if suffix.lower() == ".png":
        return "PNG"
    if m:
        return m.group(1).upper()
    return suffix.lower().lstrip(".")


@dataclass
class Record:
    path: Path
    group: str
    variant: str
    used_seed: Optional[int]
    seed_source: Optional[str]
    prompt_sha256: Optional[str]
    workflow_sha256: Optional[str]
    preset_sha256: Optional[str]


def _load_record(p: Path) -> Record:
    if p.suffix.lower() == ".png":
        chunks = read_png_text_chunks(p)
        prompt_obj, workflow_obj = extract_prompt_workflow_from_png_chunks(chunks)
    else:
        tags = ffprobe_format_tags(p)
        prompt_obj, workflow_obj = extract_prompt_workflow_from_tags(tags)

    seeds = collect_seeds_from_prompt(prompt_obj)
    preset = extract_preset(prompt_obj)
    return Record(
        path=p,
        group=_group_key(p.stem),
        variant=_variant(p.stem, p.suffix),
        used_seed=seeds.get("used_seed"),
        seed_source=seeds.get("seed_source"),
        prompt_sha256=stable_json_sha256(prompt_obj) if prompt_obj is not None else None,
        workflow_sha256=stable_json_sha256(workflow_obj) if workflow_obj is not None else None,
        preset_sha256=stable_json_sha256(preset) if preset is not None else None,
    )


def _cmp_key(r: Record) -> Tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
    # Primary comparison key for "same run parameters"
    return (r.used_seed, r.prompt_sha256, r.workflow_sha256, r.preset_sha256)


def main() -> int:
    ap = argparse.ArgumentParser(description="Check OG/UPIN/PNG embedded-metadata agreement")
    ap.add_argument("dir", help="Directory containing wip outputs (e.g. .../wip/2026-02-01)")
    ap.add_argument(
        "--strict-workflow",
        action="store_true",
        help="Fail if workflow hashes differ. (Often OK for postprocessed variants.)",
    )
    args = ap.parse_args()

    d = Path(args.dir)
    if not d.exists() or not d.is_dir():
        raise SystemExit(f"Not a directory: {d}")

    files = sorted([p for p in d.iterdir() if p.is_file() and p.suffix.lower() in {'.png', '.mp4'}])
    if not files:
        raise SystemExit(f"No .png/.mp4 files found in {d}")

    records: List[Record] = []
    errors: List[str] = []
    for p in files:
        try:
            records.append(_load_record(p))
        except Exception as e:
            errors.append(f"{p.name}: {e}")

    by_group: Dict[str, List[Record]] = {}
    for r in records:
        by_group.setdefault(r.group, []).append(r)

    mismatches: List[str] = []
    missing_png: List[str] = []

    for g, recs in sorted(by_group.items()):
        # Only care about groups that look like they have the expected variants.
        vmap = {r.variant: r for r in recs}
        want = ["PNG", "OG", "UPIN"]
        have = [v for v in want if v in vmap]
        if len(have) < 2:
            continue
        if "PNG" not in vmap:
            # In your convention, there should be exactly one PNG per OG/UPIN pair.
            missing_png.append(g)

        # Compare everything against the first available in order PNG->OG->UPIN
        base = vmap.get("PNG") or vmap.get("OG") or vmap.get("UPIN")
        assert base is not None

        for v in have:
            cur = vmap[v]
            if cur.used_seed != base.used_seed or cur.preset_sha256 != base.preset_sha256 or cur.prompt_sha256 != base.prompt_sha256:
                mismatches.append(
                    "\n".join(
                        [
                            f"{g}: mismatch {base.variant} vs {cur.variant}",
                            f"  base: {base.path.name}",
                            f"    used_seed={base.used_seed} source={base.seed_source}",
                            f"    preset_sha256={base.preset_sha256}",
                            f"    prompt_sha256={base.prompt_sha256}",
                            f"  cur : {cur.path.name}",
                            f"    used_seed={cur.used_seed} source={cur.seed_source}",
                            f"    preset_sha256={cur.preset_sha256}",
                            f"    prompt_sha256={cur.prompt_sha256}",
                        ]
                    )
                )
            if args.strict_workflow and cur.workflow_sha256 != base.workflow_sha256:
                mismatches.append(
                    "\n".join(
                        [
                            f"{g}: workflow hash differs {base.variant} vs {cur.variant}",
                            f"  base: {base.path.name} workflow_sha256={base.workflow_sha256}",
                            f"  cur : {cur.path.name} workflow_sha256={cur.workflow_sha256}",
                        ]
                    )
                )

    if errors:
        print("## Errors while reading metadata")
        for e in errors[:50]:
            print(f"- {e}")
        if len(errors) > 50:
            print(f"... and {len(errors) - 50} more")
        print("")

    if mismatches:
        print("## Mismatches")
        for mm in mismatches[:50]:
            print(mm)
            print("")
        if len(mismatches) > 50:
            print(f"... and {len(mismatches) - 50} more mismatches")
        if missing_png:
            print("## Missing PNGs")
            for g in missing_png[:200]:
                print(f"- {g}")
            if len(missing_png) > 200:
                print(f"... and {len(missing_png) - 200} more")
        return 2

    if not errors:
        print("OK: all comparable OG/UPIN/PNG groups agree on used_seed + prompt_sha256 + preset_sha256.")
    else:
        print("OK (with read errors): all comparable groups that were readable agree on used_seed + prompt_sha256 + preset_sha256.")

    if missing_png:
        print("")
        print("## Missing PNGs")
        for g in missing_png[:200]:
            print(f"- {g}")
        if len(missing_png) > 200:
            print(f"... and {len(missing_png) - 200} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

