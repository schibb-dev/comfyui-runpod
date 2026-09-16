"""Extract a compact generation spec from a Comfy API prompt or LiteGraph workflow.

Used by Queue glance, Workbench, family pickers, and (on new submits) output
``filename_prefix`` suffixes. Title-based — do not rely on FB9 node ids.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Dict, Optional

RUN_SPEC_SUFFIX_RE = re.compile(r"__rs-[A-Za-z0-9._-]+$", re.I)
_QUANT_Q_RE = re.compile(r"(?:^|[-_/.])(Q\d+)(?:[_-]|$)", re.I)
_RES_RE = re.compile(r"(?:^|[-_/.])(720p|480p|1080p|540p)(?:[-_/.]|$)", re.I)
_FP_RE = re.compile(r"(?:^|[-_/.])(fp8|fp16|bf16)(?:[_-a-z0-9]*)", re.I)

_UNET_TYPES = {
    "UnetLoaderGGUFDisTorchMultiGPU",
    "UnetLoaderGGUF",
    "UNETLoader",
    "UNETLoaderKJ",
    "CheckpointLoaderSimple",
}

_TEA_TYPES = {"WanVideoTeaCacheKJ"}
_WAN_I2V_TYPES = {"WanImageToVideo"}
_MX_SLIDER_TYPES = {"mxSlider", "mxSlider2D"}

_SIZE_TITLES = {"size", "resolution"}
_STEPS_TITLES = {"steps"}
_TEA_TITLES = {"tea cache", "teacache", "run_teacache", "tea cache kj"}
_DURATION_TITLES = {"duration"}
_FRAMES_TITLES = {"frames"}
_MODEL_TITLES = {"model"}

_PREVIEW_TOKENS = ("_preview", "-preview", "_debug", "-debug", "_raw", "-raw", "preview_debug")

_OUTPUT_PREFIX_TYPES = {
    "VHS_VideoCombine",
    "SaveImage",
    "SaveAnimatedWEBP",
    "SaveAnimatedPNG",
}


def strip_run_spec_suffix(name: str) -> str:
    """Remove a trailing ``__rs-…`` token from a prefix or basename."""
    text = str(name or "")
    if not text:
        return text
    norm = text.replace("\\", "/")
    if "/" in norm:
        parent, base = norm.rsplit("/", 1)
        return f"{parent}/{RUN_SPEC_SUFFIX_RE.sub('', base)}"
    return RUN_SPEC_SUFFIX_RE.sub("", text)


def append_run_spec_to_prefix(prefix: str, token: str) -> str:
    """Idempotently append ``__rs-{token}`` to the last path segment."""
    raw = str(prefix or "").strip()
    tok = str(token or "").strip()
    if not raw:
        return raw
    if not tok:
        return strip_run_spec_suffix(raw)
    if _is_preview_prefix(raw):
        return raw
    stripped = strip_run_spec_suffix(raw.rstrip("/"))
    return f"{stripped}__rs-{tok}"


def _is_preview_prefix(path: str) -> bool:
    stem = Path(str(path).replace("\\", "/")).stem.lower()
    return any(token in stem for token in _PREVIEW_TOKENS)


def extract_run_spec(graph: Any) -> Dict[str, Any]:
    """Return a spec dict (possibly empty) from an API prompt or LiteGraph doc."""
    if not isinstance(graph, dict):
        return {}
    if _is_litegraph(graph):
        raw = _extract_litegraph(graph)
    else:
        raw = _extract_api_prompt(graph)
    return _finalize_spec(raw)


def extract_run_spec_from_template(
    shape: Optional[Dict[str, Any]],
    *,
    data_root: Path,
    template_path: Optional[str] = None,
    job: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Load a family catalog template (runtime ui_defaults, then named stack) and extract a spec.

    ``job`` supplies ``adhoc_overrides.stack`` so the spec follows the stack
    that will actually run, not the fossil catalog UNet widgets.
    """
    try:
        from shape_factory import apply_shape_stack_ui, apply_shape_ui_defaults_ui, read_json, resolve_job_asset_path
    except Exception:
        return {}
    data_root = Path(data_root).expanduser().resolve()
    workspace_root = data_root.parent if data_root.name == ".data" else data_root
    shape = shape if isinstance(shape, dict) else {}
    template_raw = str(template_path or shape.get("template") or "").strip()
    if not template_raw:
        return {}
    resolved = resolve_job_asset_path(template_raw, data_root=data_root, workspace_root=workspace_root)
    if resolved is None or not Path(resolved).is_file():
        return {}
    try:
        workflow = read_json(Path(resolved))
    except Exception:
        return {}
    if not isinstance(workflow, dict):
        return {}
    wf = copy.deepcopy(workflow)
    if shape:
        try:
            apply_shape_ui_defaults_ui(wf, shape)
            apply_shape_stack_ui(wf, shape, job)
        except Exception:
            pass
    return extract_run_spec(wf)


def merge_run_spec_into_params(params: Dict[str, Any], spec: Optional[Dict[str, Any]]) -> None:
    """Copy extractor fields onto a key_params / glance-friendly dict (in place)."""
    if not isinstance(params, dict) or not isinstance(spec, dict):
        return
    for key in (
        "unet_name",
        "unet_family",
        "quant",
        "width",
        "height",
        "duration_sec",
        "frames",
        "steps",
        "teacache",
        "teacache_coefficients",
        "virtual_vram_gb",
        "cfg",
        "denoise",
        "spec_abbrev",
        "spec_title",
        "spec_fs",
        "spec_model",
        "spec_params",
        "spec_tune",
        "spec_sampler",
        "sampler_name",
        "scheduler",
    ):
        val = spec.get(key)
        if val is None or val == "":
            continue
        if key not in params or params[key] is None or str(params[key]).strip() == "":
            params[key] = val
    abbrev = spec.get("abbrev")
    if abbrev and not params.get("spec_abbrev"):
        params["spec_abbrev"] = abbrev
    title = spec.get("title")
    if title and not params.get("spec_title"):
        params["spec_title"] = title
    fs_token = spec.get("fs_token")
    if fs_token and not params.get("spec_fs"):
        params["spec_fs"] = fs_token
    model = spec.get("model_abbrev") or spec.get("spec_model")
    if model and not params.get("spec_model"):
        params["spec_model"] = model
    params_abbrev = spec.get("params_abbrev") or spec.get("spec_params") or spec.get("spec_tune")
    if params_abbrev and not params.get("spec_params"):
        params["spec_params"] = params_abbrev
    if params_abbrev and not params.get("spec_tune"):
        params["spec_tune"] = params_abbrev
    sampler_abbrev = spec.get("spec_sampler")
    if sampler_abbrev and not params.get("spec_sampler"):
        params["spec_sampler"] = sampler_abbrev


def stamp_prefix_with_graph_spec(prefix: str, graph: Any) -> str:
    """Append ``__rs-…`` from the stacked graph so names follow the UNet that will run."""
    token = str((extract_run_spec(graph) or {}).get("fs_token") or "").strip()
    if not token:
        return str(prefix or "")
    return append_run_spec_to_prefix(prefix, token)


def stamp_job_run_spec(job: Dict[str, Any], spec: Optional[Dict[str, Any]]) -> None:
    """Copy glance fields onto job.json from the graph after stack apply."""
    if not isinstance(job, dict) or not isinstance(spec, dict):
        return
    model = str(spec.get("spec_model") or "").strip()
    if model:
        job["spec_model"] = model
    token = str(spec.get("fs_token") or spec.get("spec_fs") or "").strip()
    if token:
        job["spec_fs"] = token
    tune = str(spec.get("spec_tune") or spec.get("spec_params") or "").strip()
    if tune:
        job["spec_tune"] = tune
    title = str(spec.get("spec_title") or spec.get("title") or "").strip()
    if title:
        job["spec_title"] = title
    unet = str(spec.get("unet_name") or "").strip()
    if unet:
        job["unet_name"] = unet


def apply_run_spec_suffix_to_prompt(prompt: Dict[str, Any]) -> list[str]:
    """Append ``__rs-…`` to final save prefixes. Idempotent. Skips preview/debug."""
    changes: list[str] = []
    if not isinstance(prompt, dict):
        return changes
    if _is_litegraph(prompt):
        return apply_run_spec_suffix_to_workflow(prompt)
    spec = extract_run_spec(prompt)
    token = str(spec.get("fs_token") or "").strip()
    if not token:
        return changes
    for nid, node in prompt.items():
        if not isinstance(node, dict):
            continue
        ctype = str(node.get("class_type") or node.get("type") or "")
        if ctype not in _OUTPUT_PREFIX_TYPES:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        raw = inputs.get("filename_prefix")
        if not isinstance(raw, str) or not raw.strip():
            continue
        if inputs.get("save_output") is False:
            continue
        new = append_run_spec_to_prefix(raw, token)
        if new != raw:
            inputs["filename_prefix"] = new
            changes.append(f"{nid}.filename_prefix: {raw!r} -> {new!r}")
    return changes


def apply_run_spec_suffix_to_workflow(workflow: Dict[str, Any]) -> list[str]:
    """Same ``__rs-…`` stamp on LiteGraph VHS/Save widgets (embedded extra_pnginfo)."""
    changes: list[str] = []
    if not isinstance(workflow, dict) or not _is_litegraph(workflow):
        return changes
    spec = extract_run_spec(workflow)
    token = str(spec.get("fs_token") or "").strip()
    if not token:
        return changes
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or node.get("class_type") or "")
        if ntype not in _OUTPUT_PREFIX_TYPES:
            continue
        widgets = node.get("widgets_values")
        if isinstance(widgets, dict):
            if widgets.get("save_output") is False:
                continue
            raw = widgets.get("filename_prefix")
            if not isinstance(raw, str) or not raw.strip():
                continue
            new = append_run_spec_to_prefix(raw, token)
            if new != raw:
                widgets["filename_prefix"] = new
                changes.append(f"{node.get('id')}.filename_prefix: {raw!r} -> {new!r}")
    return changes


def _is_litegraph(obj: Dict[str, Any]) -> bool:
    nodes = obj.get("nodes")
    return isinstance(nodes, list)


def _norm_title(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip().lower())


def _as_float(raw: Any) -> Optional[float]:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if val == val and abs(val) != float("inf") else None
    text = str(raw).strip()
    if not text:
        return None
    try:
        val = float(text)
    except ValueError:
        return None
    if val != val or abs(val) == float("inf"):
        return None
    return val


def _as_int(raw: Any) -> Optional[int]:
    val = _as_float(raw)
    if val is None:
        return None
    if abs(val - round(val)) < 1e-6:
        return int(round(val))
    return None


def _slider_current(inputs: Dict[str, Any], *, xy: str = "X") -> Optional[float]:
    isfloat = inputs.get(f"isfloat{xy}")
    prefer_f = True
    if isfloat is not None:
        try:
            prefer_f = int(isfloat) != 0 or float(isfloat) != 0
        except (TypeError, ValueError):
            prefer_f = bool(isfloat)
    primary = f"{xy}f" if prefer_f else f"{xy}i"
    secondary = f"{xy}i" if prefer_f else f"{xy}f"
    for key in (primary, secondary, f"{xy}f", f"{xy}i"):
        val = _as_float(inputs.get(key))
        if val is not None:
            return val
    return None


def _api_node_title(node: Dict[str, Any]) -> str:
    meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
    return _norm_title(meta.get("title") or node.get("title"))


def _follow_node(prompt: Dict[str, Any], raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, list) and raw:
        node = prompt.get(str(raw[0]))
        return node if isinstance(node, dict) else None
    return None


def _resolve_number(prompt: Dict[str, Any], raw: Any) -> Optional[float]:
    direct = _as_float(raw)
    if direct is not None:
        return direct
    src = _follow_node(prompt, raw)
    if not src:
        return None
    inputs = src.get("inputs") if isinstance(src.get("inputs"), dict) else {}
    ctype = str(src.get("class_type") or "")
    if ctype in _MX_SLIDER_TYPES:
        return _slider_current(inputs, xy="X")
    for key in ("value", "Xi", "Xf", "number"):
        val = _as_float(inputs.get(key))
        if val is not None:
            return val
    return None


def _extract_api_prompt(prompt: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        ctype = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        title = _api_node_title(node)

        if ctype in _UNET_TYPES or title in _MODEL_TITLES:
            name = inputs.get("unet_name") or inputs.get("ckpt_name") or inputs.get("model_name")
            if isinstance(name, str) and name.strip() and "unet_name" not in out:
                out["unet_name"] = name.strip()
            vv = _as_float(inputs.get("virtual_vram_gb"))
            if vv is not None and "virtual_vram_gb" not in out:
                out["virtual_vram_gb"] = vv

        if ctype in _MX_SLIDER_TYPES:
            if title in _SIZE_TITLES:
                w = _slider_current(inputs, xy="X")
                h = _slider_current(inputs, xy="Y")
                if w is not None:
                    out["width"] = int(round(w))
                if h is not None:
                    out["height"] = int(round(h))
            elif title in _STEPS_TITLES:
                st = _slider_current(inputs, xy="X")
                if st is not None:
                    out["steps"] = int(round(st))
            elif title in _TEA_TITLES:
                tea = _slider_current(inputs, xy="X")
                if tea is not None:
                    out["teacache"] = tea
                    out["teacache_present"] = True
            elif title in _DURATION_TITLES:
                dur = _slider_current(inputs, xy="X")
                if dur is not None:
                    out["duration_sec"] = dur
            elif title in _FRAMES_TITLES:
                fr = _slider_current(inputs, xy="X")
                if fr is not None:
                    out.setdefault("frames", int(round(fr)))

        if ctype in _TEA_TYPES:
            out["teacache_present"] = True
            coeffs = inputs.get("coefficients")
            if isinstance(coeffs, str) and coeffs.strip():
                out["teacache_coefficients"] = coeffs.strip()
            thresh = _resolve_number(prompt, inputs.get("rel_l1_thresh"))
            if thresh is not None and "teacache" not in out:
                out["teacache"] = thresh

        if ctype in _WAN_I2V_TYPES:
            w = _resolve_number(prompt, inputs.get("width"))
            h = _resolve_number(prompt, inputs.get("height"))
            length = _resolve_number(prompt, inputs.get("length"))
            if w is not None and "width" not in out:
                out["width"] = int(round(w))
            if h is not None and "height" not in out:
                out["height"] = int(round(h))
            if length is not None and "frames" not in out:
                out["frames"] = int(round(length))

        if ctype == "BasicScheduler":
            st = _resolve_number(prompt, inputs.get("steps"))
            if st is not None and "steps" not in out:
                out["steps"] = int(round(st))
            den = _resolve_number(prompt, inputs.get("denoise"))
            if den is not None and "denoise" not in out:
                out["denoise"] = den

        if ctype in {"CFGGuider", "ScheduledCFGGuidance"}:
            cfg = _resolve_number(prompt, inputs.get("cfg"))
            if cfg is not None and "cfg" not in out:
                out["cfg"] = cfg

        title_cfg = title == "cfg"
        if title_cfg and ctype in _MX_SLIDER_TYPES:
            cfg = _slider_current(inputs, xy="X")
            if cfg is not None:
                out["cfg"] = cfg
        if title == "denoise" and ctype in _MX_SLIDER_TYPES:
            den = _slider_current(inputs, xy="X")
            if den is not None:
                out["denoise"] = den

        sname = inputs.get("sampler_name")
        if isinstance(sname, str) and sname.strip() and "sampler_name" not in out:
            out["sampler_name"] = sname.strip()
        sched = inputs.get("scheduler")
        if isinstance(sched, str) and sched.strip() and "scheduler" not in out:
            out["scheduler"] = sched.strip()
    return out


def _litegraph_widgets(node: Dict[str, Any]) -> list[Any]:
    widgets = node.get("widgets_values")
    if isinstance(widgets, list):
        return widgets
    if isinstance(widgets, dict):
        # named widgets — preserve insertion order
        return list(widgets.values())
    return []


def _extract_litegraph(workflow: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for node in workflow.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ntype = str(node.get("type") or "")
        title = _norm_title(node.get("title"))
        widgets = _litegraph_widgets(node)
        named = node.get("widgets_values") if isinstance(node.get("widgets_values"), dict) else {}

        if ntype in _UNET_TYPES or title in _MODEL_TITLES:
            name = None
            if isinstance(named, dict):
                name = named.get("unet_name") or named.get("ckpt_name") or named.get("model_name")
            if not name and widgets:
                name = widgets[0]
            if isinstance(name, str) and name.strip() and "unet_name" not in out:
                out["unet_name"] = name.strip()
            vv = None
            if isinstance(named, dict) and "virtual_vram_gb" in named:
                vv = _as_float(named.get("virtual_vram_gb"))
            elif ntype == "UnetLoaderGGUFDisTorchMultiGPU" and len(widgets) > 2:
                vv = _as_float(widgets[2])
            if vv is not None and "virtual_vram_gb" not in out:
                out["virtual_vram_gb"] = vv

        if ntype in _MX_SLIDER_TYPES:
            if title in _SIZE_TITLES:
                if len(widgets) >= 4:
                    w = _as_float(widgets[1] if widgets[1] is not None else widgets[0])
                    h = _as_float(widgets[3] if widgets[3] is not None else widgets[2])
                    if w is not None:
                        out["width"] = int(round(w))
                    if h is not None:
                        out["height"] = int(round(h))
            elif title in _STEPS_TITLES:
                st = _as_float(widgets[1] if len(widgets) > 1 else (widgets[0] if widgets else None))
                if st is not None:
                    out["steps"] = int(round(st))
            elif title in _TEA_TITLES:
                tea = _as_float(widgets[1] if len(widgets) > 1 else (widgets[0] if widgets else None))
                if tea is not None:
                    out["teacache"] = tea
                    out["teacache_present"] = True
            elif title in _DURATION_TITLES:
                dur = _as_float(widgets[1] if len(widgets) > 1 else (widgets[0] if widgets else None))
                if dur is not None:
                    out["duration_sec"] = dur
            elif title in _FRAMES_TITLES:
                fr = _as_float(widgets[1] if len(widgets) > 1 else (widgets[0] if widgets else None))
                if fr is not None:
                    out.setdefault("frames", int(round(fr)))

        if ntype in _TEA_TYPES:
            out["teacache_present"] = True
            coeffs = None
            thresh = None
            if isinstance(named, dict):
                coeffs = named.get("coefficients")
                thresh = _as_float(named.get("rel_l1_thresh"))
            if coeffs is None and widgets:
                # typical order: rel_l1_thresh, start, end, cache_device, coefficients
                if len(widgets) >= 5 and isinstance(widgets[4], str):
                    coeffs = widgets[4]
                elif isinstance(widgets[-1], str):
                    coeffs = widgets[-1]
                thresh = thresh if thresh is not None else _as_float(widgets[0] if widgets else None)
            if isinstance(coeffs, str) and coeffs.strip():
                out["teacache_coefficients"] = coeffs.strip()
            if thresh is not None and "teacache" not in out:
                out["teacache"] = thresh

        if ntype in _WAN_I2V_TYPES and widgets:
            w = _as_float(widgets[0] if len(widgets) > 0 else None)
            h = _as_float(widgets[1] if len(widgets) > 1 else None)
            length = _as_float(widgets[2] if len(widgets) > 2 else None)
            if w is not None and "width" not in out:
                out["width"] = int(round(w))
            if h is not None and "height" not in out:
                out["height"] = int(round(h))
            if length is not None and "frames" not in out:
                out["frames"] = int(round(length))

        if ntype == "BasicScheduler" and widgets:
            sched0 = widgets[0]
            if isinstance(sched0, str) and sched0.strip() and "scheduler" not in out:
                out["scheduler"] = sched0.strip()
            st = _as_float(widgets[1] if len(widgets) > 1 else None)
            den = _as_float(widgets[2] if len(widgets) > 2 else None)
            if st is not None and "steps" not in out:
                out["steps"] = int(round(st))
            if den is not None and "denoise" not in out:
                out["denoise"] = den

        if ntype in {"KSamplerSelect", "KSampler", "KSamplerAdvanced"} and widgets:
            sname = widgets[0] if isinstance(widgets[0], str) else None
            if sname and sname.strip() and "sampler_name" not in out:
                out["sampler_name"] = sname.strip()

        if ntype in {"CFGGuider", "ScheduledCFGGuidance"} and widgets:
            cfg = _as_float(widgets[0])
            if cfg is not None and "cfg" not in out:
                out["cfg"] = cfg
    return out


def _parse_unet_filename(name: str) -> Dict[str, Optional[str]]:
    base = Path(str(name or "").replace("\\", "/")).name
    stem = base
    if stem.lower().endswith(".gguf"):
        stem = stem[: -len(".gguf")]
    elif stem.lower().endswith(".safetensors"):
        stem = stem[: -len(".safetensors")]
    family = None
    mres = _RES_RE.search(stem)
    if mres:
        family = mres.group(1).lower()
    elif re.search(r"(?:^|[-_])t2v(?:[-_]|$)", stem, re.I):
        family = "t2v"
    elif re.search(r"(?:^|[-_])i2v(?:[-_]|$)", stem, re.I):
        family = "i2v"
    quant = None
    mq = _QUANT_Q_RE.search(stem)
    if mq:
        quant = mq.group(1).upper()
    else:
        mfp = _FP_RE.search(stem)
        if mfp:
            quant = mfp.group(1).lower()
    return {"unet_family": family, "quant": quant}


def _fmt_duration(sec: float) -> str:
    if abs(sec - round(sec)) < 1e-6:
        return f"{int(round(sec))}s"
    text = f"{sec:.2f}".rstrip("0").rstrip(".")
    return f"{text}s"


def _fmt_tea(val: float) -> str:
    if abs(val) < 1e-9:
        return "Toff"
    text = f"{val:.3f}".rstrip("0").rstrip(".")
    if text.startswith("0"):
        text = text[1:]
    return f"T{text}" if text.startswith(".") else f"T{text}"


def _fmt_tea_fs(val: float) -> str:
    if abs(val) < 1e-9:
        return "TeaOff"
    text = f"{val:.3f}".rstrip("0").rstrip(".")
    if text.startswith("0."):
        text = text[1:]
    return "Tea" + text.replace(".", "")


def _fmt_duration_fs(sec: float) -> str:
    if abs(sec - round(sec)) < 1e-6:
        return f"{int(round(sec))}s"
    text = f"{sec:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", "p") + "s"


def _fmt_virt(val: float) -> Optional[str]:
    if abs(val) < 1e-9:
        return None
    return f"virt{val:.1f}"


def _fmt_vv_fs(val: float) -> Optional[str]:
    if abs(val) < 1e-9:
        return None
    if abs(val - round(val)) < 1e-6:
        return f"vv{int(round(val))}"
    text = f"{val:.2f}".rstrip("0").rstrip(".")
    return f"vv{text}"


def _fmt_cfg(val: float) -> str:
    return f"cfg{val:.1f}"


def _fmt_denoise(val: float) -> str:
    return f"den{val:.2f}"


def _finalize_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(raw)
    unet = str(out.get("unet_name") or "").strip()
    parsed = _parse_unet_filename(unet) if unet else {"unet_family": None, "quant": None}
    if parsed.get("unet_family") and not out.get("unet_family"):
        out["unet_family"] = parsed["unet_family"]
    if parsed.get("quant") and not out.get("quant"):
        out["quant"] = parsed["quant"]

    model_bits: list[str] = []
    tune_bits: list[str] = []
    sampler_bits: list[str] = []
    fs_bits: list[str] = []
    family = str(out.get("unet_family") or "").strip()
    quant = str(out.get("quant") or "").strip()
    head = "-".join(p for p in (family, quant) if p)
    if head:
        model_bits.append(head)
    if family:
        fs_bits.append(family)
    if quant:
        fs_bits.append(quant)

    vv = _as_float(out.get("virtual_vram_gb"))
    virt_s = None
    vv_fs = None
    if vv is not None:
        out["virtual_vram_gb"] = vv
        virt_s = _fmt_virt(vv)
        vv_fs = _fmt_vv_fs(vv)
        if virt_s:
            model_bits.append(virt_s)

    width = _as_int(out.get("width"))
    height = _as_int(out.get("height"))
    if width is not None:
        out["width"] = width
    if height is not None:
        out["height"] = height
    if width is not None and height is not None:
        model_bits.append(f"{width}×{height}")
        fs_bits.append(f"{width}x{height}")

    dur = _as_float(out.get("duration_sec"))
    frames = _as_int(out.get("frames"))
    steps = _as_int(out.get("steps"))
    if dur is not None:
        out["duration_sec"] = dur
        tune_bits.append(_fmt_duration(dur))
        fs_bits.append(_fmt_duration_fs(dur))
    elif frames is not None:
        out["frames"] = frames
        tune_bits.append(f"{frames}f")
        fs_bits.append(f"{frames}f")
    if steps is not None:
        out["steps"] = steps
        tune_bits.append(f"{steps}step")
        fs_bits.append(f"{steps}st")

    cfg = _as_float(out.get("cfg"))
    if cfg is not None:
        out["cfg"] = cfg
        tune_bits.append(_fmt_cfg(cfg))

    den = _as_float(out.get("denoise"))
    if den is not None:
        out["denoise"] = den
        tune_bits.append(_fmt_denoise(den))

    sname = str(out.get("sampler_name") or "").strip()
    sched = str(out.get("scheduler") or "").strip()
    if sname:
        sampler_bits.append(sname)
    if sched and sched != sname:
        sampler_bits.append(sched)

    tea_present = bool(out.get("teacache_present")) or out.get("teacache") is not None
    tea = _as_float(out.get("teacache"))
    if tea is not None:
        out["teacache"] = tea
        sampler_bits.append(_fmt_tea(tea))
        fs_bits.append(_fmt_tea_fs(tea))
    elif tea_present:
        sampler_bits.append("T")
        fs_bits.append("Tea")
    if vv_fs:
        fs_bits.append(vv_fs)

    spec_model = " ".join(model_bits)
    spec_params = " ".join(tune_bits)
    spec_sampler = " ".join(sampler_bits)
    abbrev = " ".join(p for p in (spec_model, spec_params, spec_sampler) if p)
    fs_token = "_".join(fs_bits) if fs_bits else ""
    # filesystem token must match __rs-[A-Za-z0-9._-]+
    fs_token = re.sub(r"[^A-Za-z0-9._-]+", "-", fs_token).strip("-")

    title_parts: list[str] = []
    if unet:
        title_parts.append(unet)
    coeffs = str(out.get("teacache_coefficients") or "").strip()
    if coeffs:
        title_parts.append(f"coeffs {coeffs}")
    cfg = out.get("cfg")
    if cfg is not None:
        title_parts.append(f"cfg {cfg}")
    den = out.get("denoise")
    if den is not None:
        title_parts.append(f"denoise {den}")
    if vv is not None:
        title_parts.append(f"virtual_vram {vv} GB")
    title = " · ".join(title_parts) if title_parts else abbrev

    out["abbrev"] = abbrev
    out["spec_abbrev"] = abbrev
    out["spec_model"] = spec_model
    out["model_abbrev"] = spec_model
    out["spec_params"] = spec_params
    out["params_abbrev"] = spec_params
    out["spec_tune"] = spec_params
    out["spec_sampler"] = spec_sampler
    out["title"] = title
    out["spec_title"] = title
    out["fs_token"] = fs_token
    out["spec_fs"] = fs_token
    return out
