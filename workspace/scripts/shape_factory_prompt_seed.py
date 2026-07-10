#!/usr/bin/env python3
"""Seed pool prompt profiles from a shape template's binding nodes (catalog workflow)."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from shape_factory import load_yaml


def _slugify(label: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", label.strip().lower()).strip("-")
    return s or "catalog-default"


def _prompt_binding(shape: dict[str, Any]) -> Optional[dict[str, Any]]:
    for req in shape.get("requires") or []:
        if not isinstance(req, dict):
            continue
        binding = req.get("binding") if isinstance(req.get("binding"), dict) else {}
        if str(binding.get("type") or "") == "prompt_bundle":
            return binding
    return None


def _node_text(workflow: dict[str, Any], node_id: int, widget_index: int = 0) -> str:
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict) or int(node.get("id") or -1) != int(node_id):
            continue
        widgets = node.get("widgets_values")
        if isinstance(widgets, list):
            if not widgets:
                return ""
            idx = min(max(0, widget_index), len(widgets) - 1)
            return str(widgets[idx] or "")
        if widgets is not None:
            return str(widgets)
        return ""
    return ""


def extract_prompt_from_template(template_path: Path, binding: dict[str, Any]) -> Tuple[str, str]:
    workflow = json.loads(template_path.read_text(encoding="utf-8"))
    pos_spec = binding.get("positive") if isinstance(binding.get("positive"), dict) else {}
    neg_spec = binding.get("negative") if isinstance(binding.get("negative"), dict) else {}
    positive = _node_text(
        workflow,
        int(pos_spec.get("node_id") or 0),
        int(pos_spec.get("widget_index") or 0),
    )
    negative = _node_text(
        workflow,
        int(neg_spec.get("node_id") or 0),
        int(neg_spec.get("widget_index") or 0),
    )
    return positive.strip(), negative.strip()


def write_prompt_profile(path: Path, *, label: str, positive: str, negative: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"label": label, "positive": positive, "negative": negative}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def seed_family(
    *,
    shape_path: Path,
    pools_dir: Path,
    label: str,
    template_override: Optional[Path] = None,
    replace_all: bool = False,
) -> Path:
    shape = load_yaml(shape_path)
    binding = _prompt_binding(shape)
    if not binding:
        raise RuntimeError(f"no prompt_bundle binding in {shape_path}")

    template = template_override or Path(str(shape.get("template") or "")).expanduser()
    if not template.is_file():
        raise FileNotFoundError(f"template not found: {template}")

    positive, negative = extract_prompt_from_template(template, binding)
    if not positive and not negative:
        raise RuntimeError(f"empty prompt extracted from {template}")

    slug = _slugify(label)
    prompts_dir = pools_dir / "prompts"
    if replace_all and prompts_dir.is_dir():
        for old in prompts_dir.glob("*.json"):
            old.unlink()

    out = prompts_dir / f"{slug}.json"
    write_prompt_profile(out, label=slug, positive=positive, negative=negative)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Seed pool prompts from catalog workflow binding nodes")
    p.add_argument("--shape", type=Path, required=True, help="Shape YAML path")
    p.add_argument("--pools", type=Path, help="Pools dir (default: sibling of shape family under .data/pools)")
    p.add_argument("--label", default="catalog-default", help="Profile label / filename stem")
    p.add_argument("--template", type=Path, help="Override template JSON (default: shape.template)")
    p.add_argument(
        "--replace-all",
        action="store_true",
        help="Remove existing prompts/*.json in this family before writing",
    )
    args = p.parse_args()

    shape_path = args.shape.expanduser().resolve()
    shape = load_yaml(shape_path)
    family = str(shape.get("family_slug") or shape_path.stem.replace(".shape", ""))
    pools_dir = args.pools.expanduser().resolve() if args.pools else shape_path.parent.parent / "pools" / family

    out = seed_family(
        shape_path=shape_path,
        pools_dir=pools_dir,
        label=str(args.label),
        template_override=args.template.expanduser().resolve() if args.template else None,
        replace_all=bool(args.replace_all),
    )
    print(f"family={family}")
    print(f"prompt_profile={out}")
    print(f"positive_chars={len(json.loads(out.read_text())['positive'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
