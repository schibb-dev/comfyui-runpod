#!/usr/bin/env python3
"""
Generalized workflow repair: pattern → fix → retry.

Rules inspect LiteGraph workflows and/or converted API prompts plus validation
reports, apply fixes when matched, and repeat until stable or max rounds.
"""

from __future__ import annotations

import copy
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from output_path_lib import (  # noqa: E402
    normalize_prompt_output_prefixes,
    normalize_ui_workflow_output_prefixes,
)

DEFAULT_NODE_TYPE_MAP = (
    Path(__file__).resolve().parents[2] / "scripts" / "workflow_node_id_map.yaml"
)
DEFAULT_REPAIR_RULES_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "workflow_repair_rules.yaml"
)

UI_ONLY_NODE_TYPES = frozenset(
    {
        "PrimitiveNode",
        "Note",
        "MarkdownNote",
        "Reroute",
        "Fast Groups Bypasser (rgthree)",
        "Fast Groups Muter (rgthree)",
        "Fast Groups Bypasser",
        "Fast Groups Muter",
    }
)

IMAGE_OUTPUT_CLASS_TYPES = frozenset(
    {
        "ImageFromBatch",
        "LoadImage",
        "VHS_LoadVideo",
        "VHS_LoadVideoPath",
        "ImageScale",
        "ImageListToImageBatch",
    }
)

STRING_INPUT_SANITIZE_NODES: dict[str, tuple[str, ...]] = {
    "Text Concatenate": ("text_a", "text_b"),
    "StringConcatenate": ("string_a", "string_b"),
}


@dataclass
class RepairFix:
    rule_id: str
    phase: str
    summary: str
    node_id: Any = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "phase": self.phase,
            "summary": self.summary,
            "node_id": self.node_id,
            "details": self.details,
        }


@dataclass
class RepairContext:
    workflow: dict[str, Any]
    object_info: Optional[dict[str, Any]] = None
    map_path: Optional[Path] = None
    repair_rules_path: Optional[Path] = None
    data_root: Optional[Path] = None
    prompt: Optional[dict[str, Any]] = None
    report: Optional[dict[str, Any]] = None
    ui_only_types: frozenset[str] = UI_ONLY_NODE_TYPES

    def copy_workflow(self) -> dict[str, Any]:
        return copy.deepcopy(self.workflow)


class RepairRule(Protocol):
    rule_id: str
    phase: str

    def matches(self, ctx: RepairContext) -> bool: ...

    def apply(self, ctx: RepairContext) -> list[RepairFix]: ...


def load_type_mappings(map_path: Optional[Path] = None) -> dict[str, str]:
    path = (map_path or DEFAULT_NODE_TYPE_MAP).expanduser().resolve()
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    mappings = raw.get("mappings") or {}
    out = {str(k): str(v) for k, v in mappings.items() if k and v and str(k) != str(v)}
    out.setdefault("LoadImageWithFilename|pysssss", "LoadImage")
    return out


def _patch_load_image_with_filename(node: dict[str, Any], new_type: str) -> list[str]:
    details: list[str] = []
    node["type"] = new_type
    props = node.get("properties")
    if not isinstance(props, dict):
        props = {}
        node["properties"] = props
    if props.get("Node name for S&R") in {None, "", "LoadImageWithFilename|pysssss"}:
        props["Node name for S&R"] = new_type
        details.append("properties.Node name for S&R")
    outputs = node.get("outputs")
    if isinstance(outputs, list) and len(outputs) > 2:
        node["outputs"] = outputs[:2]
        details.append("outputs trimmed to 2")
    widgets = node.get("widgets_values")
    if isinstance(widgets, list) and len(widgets) == 1:
        widgets.append("image")
        details.append("widgets_values upload default")
    return details


POST_TYPE_RENAME_HOOKS: dict[tuple[str, str], Callable[[dict[str, Any], str], list[str]]] = {
    ("LoadImageWithFilename|pysssss", "LoadImage"): _patch_load_image_with_filename,
}

_ASSET_NODE_TYPES = frozenset({"LoadImage", "VHS_LoadVideo", "VHS_LoadVideoPath"})
_INVALID_ASSET_RE = re.compile(r"Invalid (?:image|video) file: (.+)$", re.IGNORECASE)
_HASH_IN_NAME_RE = re.compile(r"[a-f0-9]{24,}", re.IGNORECASE)


def _comfy_path_variants(name: str) -> list[str]:
    """Comfy UI paths vs on-disk layout may differ by repeated output/ segments."""
    name = str(name or "").strip().replace("\\", "/").lstrip("/")
    if not name:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def push(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            out.append(value)

    push(name)
    cur = name
    while "output/output/" in cur:
        cur = cur.replace("output/output/", "output/", 1)
        push(cur)
    if (cur.startswith("output/og/") or cur.startswith("output/wip/")) and not cur.startswith("output/output/"):
        push(f"output/{cur}")
    return out


def _stage_asset_in_input(path: Path, input_root: Path) -> str:
    """Symlink or copy into input/; return basename for combo-style widgets."""
    input_root.mkdir(parents=True, exist_ok=True)
    link = input_root / path.name
    if link.is_symlink():
        try:
            if not link.resolve().is_file():
                link.unlink()
        except OSError:
            link.unlink()
    elif link.exists() and not link.is_file():
        link.unlink()
    if not link.is_file():
        try:
            link.symlink_to(os.path.relpath(path, input_root))
        except OSError:
            import shutil

            shutil.copy2(path, link)
    return path.name


def _comfy_asset_relpath(path: Path, data_root: Path, *, class_type: str = "") -> str:
    path = path.expanduser().resolve()
    data_root = data_root.expanduser().resolve()
    input_root = data_root / "input"
    output_root = data_root / "output"
    class_type = str(class_type or "")

    if class_type in {"LoadImage", "VHS_LoadVideo"}:
        try:
            if path.is_relative_to(input_root):
                rel = path.relative_to(input_root)
                return path.name if rel.parent == Path(".") else rel.as_posix()
            return _stage_asset_in_input(path, input_root)
        except (AttributeError, ValueError, OSError):
            pass
        return path.name

    if class_type == "VHS_LoadVideoPath":
        try:
            if path.is_relative_to(output_root):
                return f"output/{path.relative_to(output_root).as_posix()}"
            if path.is_relative_to(input_root):
                return f"input/{path.relative_to(input_root).as_posix()}"
        except AttributeError:
            pass
        return path.name

    try:
        if path.is_relative_to(input_root):
            rel = path.relative_to(input_root)
            return path.name if rel.parent == Path(".") else f"input/{rel.as_posix()}"
        if path.is_relative_to(output_root):
            return path.relative_to(output_root).as_posix()
    except AttributeError:
        pass
    return path.name


def _asset_search_roots(data_root: Path) -> list[Path]:
    data_root = data_root.expanduser().resolve()
    roots = [data_root / "input", data_root / "output"]
    return [r for r in roots if r.is_dir()]


def _hash_tokens(name: str) -> list[str]:
    return [m.group(0).lower() for m in _HASH_IN_NAME_RE.finditer(name)]


def _resolve_missing_asset(name: str, data_root: Path) -> Optional[Path]:
    name = str(name or "").strip().replace("\\", "/")
    if not name or name.startswith("[") or "None" in name:
        return None
    basename = Path(name).name
    data_root = data_root.expanduser().resolve()

    for root in _asset_search_roots(data_root):
        direct = root / basename
        if direct.is_file():
            return direct
        # shallow name match anywhere under input/output (OG artifacts live in nested og/ dirs)
        for path in sorted(root.rglob(basename)):
            if path.is_file():
                return path

    tokens = _hash_tokens(basename)
    if not tokens:
        return None
    best: Optional[Path] = None
    best_score = -1
    for root in _asset_search_roots(data_root):
        for token in tokens:
            if len(token) < 24:
                continue
            for path in root.rglob(f"*{token}*"):
                if not path.is_file():
                    continue
                score = 0
                if path.name == basename:
                    score += 100
                if token in path.name.lower():
                    score += 50
                if path.suffix.lower() == Path(basename).suffix.lower():
                    score += 10
                if score > best_score:
                    best_score = score
                    best = path
    return best


def _read_node_asset_name(node: dict[str, Any]) -> Optional[str]:
    class_type = str(node.get("type") or "")
    if class_type == "LoadImage":
        widgets = node.get("widgets_values")
        if isinstance(widgets, list) and widgets:
            return str(widgets[0])
    if class_type in {"VHS_LoadVideo", "VHS_LoadVideoPath"}:
        widgets = node.get("widgets_values")
        if isinstance(widgets, dict):
            video = widgets.get("video")
            if isinstance(video, str) and video.strip():
                return video.strip()
    return None


def _write_node_asset_name(node: dict[str, Any], comfy_path: str) -> None:
    class_type = str(node.get("type") or "")
    if class_type == "LoadImage":
        widgets = node.get("widgets_values")
        if isinstance(widgets, list):
            if widgets:
                widgets[0] = comfy_path
            else:
                widgets.append(comfy_path)
            if len(widgets) == 1:
                widgets.append("image")
        else:
            node["widgets_values"] = [comfy_path, "image"]
    elif class_type in {"VHS_LoadVideo", "VHS_LoadVideoPath"}:
        widgets = node.get("widgets_values")
        if not isinstance(widgets, dict):
            widgets = {}
            node["widgets_values"] = widgets
        widgets["video"] = comfy_path
        preview = widgets.get("videopreview")
        if isinstance(preview, dict):
            params = preview.get("params")
            if isinstance(params, dict):
                params["filename"] = comfy_path


def _exact_asset_path(name: str, data_root: Path) -> Optional[Path]:
    data_root = data_root.expanduser().resolve()
    for variant in _comfy_path_variants(name):
        candidate = Path(variant)
        if candidate.is_file():
            return candidate.resolve()
        if variant.startswith(("output/", "input/")):
            rooted = data_root / variant
            if rooted.is_file():
                return rooted
        basename = Path(variant).name
        if not basename:
            continue
        for root in _asset_search_roots(data_root):
            direct = root / basename
            if direct.is_file():
                return direct
    return None


def _asset_widget_needs_fixup(name: str, class_type: str, data_root: Path) -> bool:
    """True when the widget path exists on disk but uses the wrong Comfy-facing form."""
    name = str(name or "").strip().replace("\\", "/")
    if not name:
        return False
    resolved = _exact_asset_path(name, data_root) or _resolve_missing_asset(name, data_root)
    if not resolved:
        return False
    class_type = str(class_type or "")
    expected = _comfy_asset_relpath(resolved, data_root, class_type=class_type)
    if expected != name:
        return True
    if class_type == "LoadImage" and name != Path(name).name:
        return True
    if class_type in {"VHS_LoadVideo", "VHS_LoadVideoPath"} and ("/" in name or name.startswith("output/")):
        if class_type == "VHS_LoadVideo":
            return True
    return False


def _asset_needs_remap(name: str, data_root: Path, *, class_type: str = "") -> bool:
    """True when widget filename needs remap to a Comfy-valid path."""
    if _asset_widget_needs_fixup(name, class_type, data_root):
        return True
    if _exact_asset_path(name, data_root):
        return False
    return _resolve_missing_asset(name, data_root) is not None


def _invalid_asset_errors(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (node_id, class_type, missing_filename) from validation report."""
    out: list[tuple[str, str, str]] = []
    node_errors = report.get("node_errors") if isinstance(report.get("node_errors"), dict) else {}
    for node_id, block in node_errors.items():
        if not isinstance(block, dict):
            continue
        class_type = str(block.get("class_type") or "")
        for err in block.get("errors") or []:
            if not isinstance(err, dict):
                continue
            if err.get("type") != "custom_validation_failed":
                continue
            m = _INVALID_ASSET_RE.search(str(err.get("details") or ""))
            if m:
                out.append((str(node_id), class_type, m.group(1).strip()))
    return out


def _missing_asset_targets(ctx: RepairContext) -> list[tuple[str, dict[str, Any], str]]:
    """Return (node_id, node, missing_asset_name)."""
    if not ctx.data_root:
        return []
    data_root = Path(ctx.data_root)
    targets: list[tuple[str, dict[str, Any], str]] = []
    seen: set[tuple[str, str]] = set()

    report = ctx.report if isinstance(ctx.report, dict) else {}
    for node_id, _class_type, missing in _invalid_asset_errors(report):
        key = (node_id, missing)
        if key in seen:
            continue
        node = next(
            (n for n in ctx.workflow.get("nodes") or [] if isinstance(n, dict) and str(n.get("id")) == node_id),
            None,
        )
        if isinstance(node, dict):
            seen.add(key)
            targets.append((node_id, node, missing))

    for node in ctx.workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("type") or "")
        if class_type not in _ASSET_NODE_TYPES:
            continue
        name = _read_node_asset_name(node)
        class_type = str(node.get("type") or "")
        if not name or not _asset_needs_remap(name, data_root, class_type=class_type):
            continue
        if not (_exact_asset_path(name, data_root) or _resolve_missing_asset(name, data_root)):
            continue
        node_id = str(node.get("id"))
        key = (node_id, name)
        if key in seen:
            continue
        seen.add(key)
        targets.append((node_id, node, name))
    return targets


class MissingAssetRemapRule:
    """Resolve LoadImage / VHS paths by hash suffix search under data_root."""

    rule_id = "missing_asset_remap"
    phase = "ui_workflow"

    def matches(self, ctx: RepairContext) -> bool:
        return bool(_missing_asset_targets(ctx))

    def apply(self, ctx: RepairContext) -> list[RepairFix]:
        if not ctx.data_root:
            return []
        data_root = Path(ctx.data_root)
        fixes: list[RepairFix] = []
        for node_id, node, missing in _missing_asset_targets(ctx):
            resolved = _exact_asset_path(missing, data_root) or _resolve_missing_asset(missing, data_root)
            if not resolved:
                continue
            class_type = str(node.get("type") or "")
            comfy_path = _comfy_asset_relpath(resolved, data_root, class_type=class_type)
            if comfy_path == missing:
                continue
            _write_node_asset_name(node, comfy_path)
            fixes.append(
                RepairFix(
                    rule_id=self.rule_id,
                    phase=self.phase,
                    summary=f"remap {node.get('type')}:{node_id} {missing!r} -> {comfy_path!r}",
                    node_id=node.get("id"),
                    details={
                        "missing": missing,
                        "resolved_host_path": str(resolved),
                        "comfy_path": comfy_path,
                    },
                )
            )
        return fixes


class NodeTypeRenameRule:
    rule_id = "node_type_rename"
    phase = "ui_workflow"

    def __init__(self, map_path: Optional[Path] = None) -> None:
        self._map_path = map_path

    def _mappings(self, ctx: RepairContext) -> dict[str, str]:
        return load_type_mappings(ctx.map_path or self._map_path)

    def matches(self, ctx: RepairContext) -> bool:
        mappings = self._mappings(ctx)
        if not mappings:
            return False
        registered = set(ctx.object_info.keys()) if isinstance(ctx.object_info, dict) else set()
        for node in ctx.workflow.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            old_type = str(node.get("type") or "")
            if old_type in mappings and (old_type not in registered or old_type == "LoadImageWithFilename|pysssss"):
                return True
        return False

    def apply(self, ctx: RepairContext) -> list[RepairFix]:
        mappings = self._mappings(ctx)
        registered = set(ctx.object_info.keys()) if isinstance(ctx.object_info, dict) else set()
        fixes: list[RepairFix] = []
        for node in ctx.workflow.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            old_type = str(node.get("type") or "")
            new_type = mappings.get(old_type)
            if not new_type or old_type == new_type:
                continue
            if old_type in registered and old_type != "LoadImageWithFilename|pysssss":
                continue
            hook = POST_TYPE_RENAME_HOOKS.get((old_type, new_type))
            structural: list[str] = []
            if hook:
                structural = hook(node, new_type)
            else:
                node["type"] = new_type
                props = node.get("properties")
                if isinstance(props, dict) and props.get("Node name for S&R") == old_type:
                    props["Node name for S&R"] = new_type
                    structural.append("properties.Node name for S&R")
            fixes.append(
                RepairFix(
                    rule_id=self.rule_id,
                    phase=self.phase,
                    summary=f"{old_type!r} -> {new_type!r}",
                    node_id=node.get("id"),
                    details={"old_type": old_type, "new_type": new_type, "structural": structural},
                )
            )
        return fixes


def load_prompt_error_rules(rules_path: Optional[Path] = None) -> list[dict[str, Any]]:
    path = (rules_path or DEFAULT_REPAIR_RULES_PATH).expanduser().resolve()
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return []
    rules = raw.get("prompt_error_rules") or []
    out: list[dict[str, Any]] = []
    for item in rules:
        if isinstance(item, dict) and item.get("id"):
            out.append(item)
    return out


def _ui_node_class_by_id(workflow: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in workflow.get("nodes") or []:
        if isinstance(node, dict) and node.get("id") is not None:
            out[str(node.get("id"))] = str(node.get("type") or "")
    return out


def _resolve_prompt_action(
    rule: dict[str, Any],
    *,
    class_type: str,
) -> str:
    by_class = rule.get("action_by_class_type")
    if isinstance(by_class, dict):
        action = by_class.get(class_type)
        if isinstance(action, str) and action.strip():
            return action.strip()
    action = rule.get("action") or rule.get("default_action")
    return str(action or "set_string_input_empty")


def _apply_prompt_input_action(
    prompt: dict[str, Any],
    node_id: str,
    input_name: str,
    action: str,
) -> bool:
    api_node = prompt.get(node_id)
    if not isinstance(api_node, dict):
        return False
    inputs = api_node.setdefault("inputs", {})
    if action == "drop_string_input":
        if input_name in inputs:
            inputs.pop(input_name, None)
            return True
        return False
    if action == "set_string_input_empty":
        if inputs.get(input_name) == "":
            return False
        inputs[input_name] = ""
        return True
    return False


def _error_input_name(err: dict[str, Any]) -> str:
    extra = err.get("extra_info") if isinstance(err.get("extra_info"), dict) else {}
    name = str(extra.get("input_name") or "")
    if name:
        return name
    details = str(err.get("details") or "").strip()
    if details in {"string_a", "string_b", "text_a", "text_b"}:
        return details
    if "," in details:
        return details.split(",", 1)[0].strip()
    return details


def _error_matches_spec(err: dict[str, Any], match_spec: dict[str, Any]) -> bool:
    if not isinstance(match_spec, dict):
        return False
    err_type = str(err.get("type") or "")
    if match_spec.get("error_type") and err_type != match_spec.get("error_type"):
        return False
    details = str(err.get("details") or "")
    contains = match_spec.get("details_contains")
    if isinstance(contains, str):
        contains = [contains]
    if isinstance(contains, list):
        for needle in contains:
            if str(needle) not in details:
                return False
    input_name = _error_input_name(err)
    name_in = match_spec.get("input_name_in")
    if isinstance(name_in, list) and name_in:
        if input_name not in [str(x) for x in name_in]:
            return False
    if match_spec.get("input_name") and input_name != str(match_spec.get("input_name")):
        return False
    return True


def _proactive_image_string_targets(
    workflow: dict[str, Any],
    prompt: dict[str, Any],
    rule: dict[str, Any],
) -> list[tuple[str, str, list[Any]]]:
    ui_types = rule.get("ui_node_types")
    if not isinstance(ui_types, list):
        ui_types = list(STRING_INPUT_SANITIZE_NODES.keys())
    input_names = rule.get("inputs")
    if not isinstance(input_names, list):
        input_names = ["string_a", "string_b", "text_a", "text_b"]

    output_types: dict[str, str] = {}
    for key, node in prompt.items():
        if isinstance(node, dict):
            output_types[str(key)] = str(node.get("class_type") or "")

    targets: list[tuple[str, str, list[Any]]] = []
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("type") or "")
        if class_type not in [str(t) for t in ui_types]:
            continue
        nid = str(node.get("id"))
        api_node = prompt.get(nid)
        if not isinstance(api_node, dict):
            continue
        inputs = api_node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for input_name in input_names:
            linked = inputs.get(str(input_name))
            if not isinstance(linked, list) or len(linked) < 1:
                continue
            src_id = str(linked[0])
            src_class = output_types.get(src_id, "")
            if src_id not in prompt or src_class in IMAGE_OUTPUT_CLASS_TYPES:
                targets.append((nid, str(input_name), linked))
    return targets


class FlattenLibraryOutputPrefixRule:
    """Strip redundant output/ prefix from save-node widgets in LiteGraph workflows."""

    rule_id = "flatten_library_output_prefix"
    phase = "ui_workflow"

    def matches(self, ctx: RepairContext) -> bool:
        probe = ctx.copy_workflow()
        return bool(normalize_ui_workflow_output_prefixes(probe))

    def apply(self, ctx: RepairContext) -> list[RepairFix]:
        changes = normalize_ui_workflow_output_prefixes(ctx.workflow)
        return [
            RepairFix(
                rule_id=self.rule_id,
                phase=self.phase,
                summary=line,
                details={"change": line},
            )
            for line in changes
        ]


class FlattenLibraryOutputPrefixPromptRule:
    """Strip redundant output/ prefix from API prompt filename_prefix inputs."""

    rule_id = "flatten_library_output_prefix"
    phase = "prompt"

    def matches(self, ctx: RepairContext) -> bool:
        if not isinstance(ctx.prompt, dict):
            return False
        probe = copy.deepcopy(ctx.prompt)
        return bool(normalize_prompt_output_prefixes(probe))

    def apply(self, ctx: RepairContext) -> list[RepairFix]:
        if not isinstance(ctx.prompt, dict):
            return []
        changes = normalize_prompt_output_prefixes(ctx.prompt)
        return [
            RepairFix(
                rule_id=self.rule_id,
                phase=self.phase,
                summary=line,
                details={"change": line},
            )
            for line in changes
        ]


class DeclarativePromptErrorRules:
    """YAML-driven prompt rules (scripts/workflow_repair_rules.yaml)."""

    rule_id = "declarative_prompt_error_rules"
    phase = "prompt"

    def __init__(self, rules_path: Optional[Path] = None) -> None:
        self._rules_path = rules_path
        self._rules = load_prompt_error_rules(rules_path)

    def reload(self) -> None:
        self._rules = load_prompt_error_rules(self._rules_path)

    def rule_ids(self) -> list[str]:
        return [str(r.get("id") or "") for r in self._rules if r.get("id")]

    def matches(self, ctx: RepairContext) -> bool:
        if not isinstance(ctx.prompt, dict):
            return False
        for rule in self._rules:
            if rule.get("proactive") and _proactive_image_string_targets(ctx.workflow, ctx.prompt, rule):
                return True
        report = ctx.report if isinstance(ctx.report, dict) else {}
        node_errors = report.get("node_errors") if isinstance(report.get("node_errors"), dict) else {}
        if not node_errors:
            return False
        ui_classes = _ui_node_class_by_id(ctx.workflow)
        for node_id, err_block in node_errors.items():
            if not isinstance(err_block, dict):
                continue
            class_type = str(err_block.get("class_type") or ui_classes.get(str(node_id), ""))
            for err in err_block.get("errors") or []:
                if not isinstance(err, dict):
                    continue
                for rule in self._rules:
                    if rule.get("proactive"):
                        continue
                    match_spec = rule.get("match")
                    if isinstance(match_spec, dict) and _error_matches_spec(err, match_spec):
                        return True
                    # allow matching by class_type on reactive rules
                    allowed = rule.get("class_type_in")
                    if (
                        isinstance(allowed, list)
                        and class_type in [str(x) for x in allowed]
                        and _error_matches_spec(err, match_spec or {})
                    ):
                        return True
        return False

    def apply(self, ctx: RepairContext) -> list[RepairFix]:
        if not isinstance(ctx.prompt, dict):
            return []
        fixes: list[RepairFix] = []
        ui_classes = _ui_node_class_by_id(ctx.workflow)
        applied: set[tuple[str, str]] = set()

        for rule in self._rules:
            rule_key = str(rule.get("id") or "prompt_rule")
            if rule.get("proactive"):
                action_name = str(rule.get("action") or "sanitize_image_string_inputs")
                if action_name == "sanitize_image_string_inputs":
                    for nid, input_name, linked in _proactive_image_string_targets(ctx.workflow, ctx.prompt, rule):
                        key = (nid, input_name)
                        if key in applied:
                            continue
                        class_type = ui_classes.get(nid, "")
                        action = _resolve_prompt_action(rule, class_type=class_type)
                        if _apply_prompt_input_action(ctx.prompt, nid, input_name, action):
                            applied.add(key)
                            fixes.append(
                                RepairFix(
                                    rule_id=rule_key,
                                    phase=self.phase,
                                    summary=f"{action} {nid}.{input_name} (proactive)",
                                    node_id=nid,
                                    details={
                                        "input_name": input_name,
                                        "was": linked,
                                        "action": action,
                                        "proactive": True,
                                    },
                                )
                            )
                continue

            report = ctx.report if isinstance(ctx.report, dict) else {}
            node_errors = report.get("node_errors") if isinstance(report.get("node_errors"), dict) else {}
            match_spec = rule.get("match") if isinstance(rule.get("match"), dict) else {}
            for node_id, err_block in node_errors.items():
                if not isinstance(err_block, dict):
                    continue
                nid = str(node_id)
                class_type = str(err_block.get("class_type") or ui_classes.get(nid, ""))
                for err in err_block.get("errors") or []:
                    if not isinstance(err, dict) or not _error_matches_spec(err, match_spec):
                        continue
                    input_name = _error_input_name(err)
                    if not input_name:
                        continue
                    key = (nid, input_name)
                    if key in applied:
                        continue
                    action = _resolve_prompt_action(rule, class_type=class_type)
                    if _apply_prompt_input_action(ctx.prompt, nid, input_name, action):
                        applied.add(key)
                        fixes.append(
                            RepairFix(
                                rule_id=rule_key,
                                phase=self.phase,
                                summary=f"{action} {nid}.{input_name} (reactive)",
                                node_id=nid,
                                details={
                                    "input_name": input_name,
                                    "action": action,
                                    "error_type": err.get("type"),
                                    "error_details": err.get("details"),
                                },
                            )
                        )
        return fixes


class PromptStringImageMismatchRule:
    """Legacy alias — delegates to declarative YAML rules when loaded."""

    rule_id = "prompt_string_image_mismatch"
    phase = "prompt"

    def __init__(self, rules_path: Optional[Path] = None) -> None:
        self._delegate = DeclarativePromptErrorRules(rules_path=rules_path)

    def matches(self, ctx: RepairContext) -> bool:
        return self._delegate.matches(ctx)

    def apply(self, ctx: RepairContext) -> list[RepairFix]:
        return self._delegate.apply(ctx)


def sanitize_prompt_string_inputs(workflow: dict[str, Any], prompt: dict[str, Any]) -> list[RepairFix]:
    delegate = DeclarativePromptErrorRules()
    ctx = RepairContext(workflow=workflow, prompt=prompt)
    return delegate.apply(ctx)


def default_repair_rules(
    map_path: Optional[Path] = None,
    repair_rules_path: Optional[Path] = None,
) -> list[RepairRule]:
    return [
        NodeTypeRenameRule(map_path=map_path),
        MissingAssetRemapRule(),
        FlattenLibraryOutputPrefixRule(),
        DeclarativePromptErrorRules(rules_path=repair_rules_path),
        FlattenLibraryOutputPrefixPromptRule(),
    ]


def sweep_rules(ctx: RepairContext, rules: list[RepairRule], *, phase: Optional[str] = None) -> list[RepairFix]:
    fixes: list[RepairFix] = []
    for rule in rules:
        if phase and rule.phase != phase:
            continue
        if not rule.matches(ctx):
            continue
        fixes.extend(rule.apply(ctx))
    return fixes


def repair_ui_until_stable(
    ctx: RepairContext,
    rules: list[RepairRule],
    *,
    max_sweeps: int = 20,
) -> list[RepairFix]:
    all_fixes: list[RepairFix] = []
    ui_rules = [r for r in rules if r.phase == "ui_workflow"]
    for _ in range(max_sweeps):
        batch = sweep_rules(ctx, ui_rules)
        if not batch:
            break
        all_fixes.extend(batch)
    return all_fixes


@dataclass
class RepairLoopResult:
    fixes: list[RepairFix] = field(default_factory=list)
    rounds: int = 0
    stable: bool = True

    def fixes_as_dicts(self) -> list[dict[str, Any]]:
        return [f.as_dict() for f in self.fixes]


def repair_until_stable(
    ctx: RepairContext,
    *,
    rules: Optional[list[RepairRule]] = None,
    validate_fn: Optional[Callable[[RepairContext], dict[str, Any]]] = None,
    max_rounds: int = 5,
) -> RepairLoopResult:
    """
    Pattern → fix → retry loop.

    Each round:
      1. Sweep UI workflow rules until no UI fixes apply.
      2. If validate_fn provided, run validation (convert + optional comfy-check).
      3. Sweep prompt rules; if any apply and validate_fn supports retry, re-validate.

    Stops when a full round applies zero fixes or validation report is ok.
    """
    rule_list = rules or default_repair_rules(ctx.map_path, ctx.repair_rules_path)
    all_fixes: list[RepairFix] = []
    ui_rules = [r for r in rule_list if r.phase == "ui_workflow"]
    prompt_rules = [r for r in rule_list if r.phase == "prompt"]

    if validate_fn is None:
        ui_fixes = repair_ui_until_stable(ctx, ui_rules)
        return RepairLoopResult(fixes=ui_fixes, rounds=1 if ui_fixes else 0, stable=not ui_fixes)

    report: dict[str, Any] = {}
    stable = False
    for round_idx in range(1, max_rounds + 1):
        round_fixes: list[RepairFix] = []
        round_fixes.extend(repair_ui_until_stable(ctx, ui_rules))

        ctx.report = validate_fn(ctx)
        report = ctx.report
        prompt_batch = sweep_rules(ctx, prompt_rules)
        round_fixes.extend(prompt_batch)

        if prompt_batch and isinstance(ctx.prompt, dict):
            ctx.report = validate_fn(ctx)
            report = ctx.report

        all_fixes.extend(round_fixes)
        if not round_fixes:
            stable = True
            break
        if report.get("ok"):
            stable = True
            break

    return RepairLoopResult(fixes=all_fixes, rounds=round_idx, stable=stable)


# Backward-compatible helpers (used by tests and legacy imports)
def apply_workflow_compat_patches(
    workflow: dict[str, Any],
    *,
    object_info: Optional[dict[str, Any]] = None,
    map_path: Optional[Path] = None,
    only_missing: bool = True,
    in_place: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    del only_missing  # rename rule respects object_info registration
    data = workflow if in_place else copy.deepcopy(workflow)
    ctx = RepairContext(workflow=data, object_info=object_info, map_path=map_path)
    fixes = repair_ui_until_stable(ctx, default_repair_rules(map_path, None))
    legacy = [
        {
            "node_id": f.node_id,
            "old_type": (f.details or {}).get("old_type"),
            "new_type": (f.details or {}).get("new_type"),
            "title": None,
            "structural": (f.details or {}).get("structural") or [],
            "rule_id": f.rule_id,
        }
        for f in fixes
    ]
    return ctx.workflow, legacy


def patchable_missing_types(
    workflow: dict[str, Any],
    object_info: dict[str, Any],
    *,
    map_path: Optional[Path] = None,
) -> list[str]:
    mappings = load_type_mappings(map_path)
    missing: set[str] = set()
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        old_type = str(node.get("type") or "")
        if not old_type or old_type in object_info:
            continue
        if old_type in mappings:
            missing.add(old_type)
    return sorted(missing)
