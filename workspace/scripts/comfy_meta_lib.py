"""
Shared helpers for ComfyUI workflow/media metadata utilities.

This module exists to keep our scripts small and consistent:
- PNG text chunk parsing (tEXt / zTXt / iTXt)
- ffprobe format/tags retrieval
- JSON parsing for double-encoded muxer tags
- extracting ComfyUI prompt/workflow JSON from tags/chunks
- compact preset extraction from resolved prompt JSON
- stable JSON hashing helpers
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def maybe_json(s: Any) -> Any:
    """
    Parse a value that might be:
    - raw JSON object/array string
    - JSON-string-wrapped JSON string (double-encoded), e.g. "\"{...}\""
    """
    if not isinstance(s, str):
        return None
    ss = s.strip()
    if not ss:
        return None
    try:
        if ss.startswith("{") or ss.startswith("["):
            return json.loads(ss)
        if ss.startswith('"'):
            inner = json.loads(ss)
            if isinstance(inner, str):
                return maybe_json(inner)
            return inner
    except Exception:
        return None
    return None


def read_png_text_chunks(png_path: Path) -> Dict[str, str]:
    data = png_path.read_bytes()
    if data[:8] != PNG_MAGIC:
        raise ValueError(f"Not a PNG: {png_path}")

    off = 8
    out: Dict[str, str] = {}
    while off + 8 <= len(data):
        length = struct.unpack(">I", data[off : off + 4])[0]
        ctype = data[off + 4 : off + 8]
        cdata = data[off + 8 : off + 8 + length]
        off = off + 12 + length

        if ctype == b"tEXt":
            k, v = cdata.split(b"\x00", 1)
            out[k.decode("latin1", "replace")] = v.decode("utf-8", "replace")
        elif ctype == b"zTXt":
            k, rest = cdata.split(b"\x00", 1)
            compressed = rest[1:]
            try:
                v = zlib.decompress(compressed).decode("utf-8", "replace")
            except Exception:
                v = ""
            out[k.decode("latin1", "replace")] = v
        elif ctype == b"iTXt":
            i = cdata.find(b"\x00")
            if i == -1:
                continue
            keyword = cdata[:i].decode("latin1", "replace")
            comp_flag = cdata[i + 1]
            j = i + 3
            k0 = cdata.find(b"\x00", j)
            if k0 == -1:
                continue
            j = k0 + 1
            k1 = cdata.find(b"\x00", j)
            if k1 == -1:
                continue
            text_bytes = cdata[k1 + 1 :]
            if comp_flag == 1:
                try:
                    text_bytes = zlib.decompress(text_bytes)
                except Exception:
                    text_bytes = b""
            out[keyword] = text_bytes.decode("utf-8", "replace")

    return out


def ffprobe_show_format(media_path: Path) -> Dict[str, Any]:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(media_path)]
    # ffprobe may emit UTF-8 even on Windows consoles; force utf-8 to avoid decode crashes.
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def ffprobe_format_tags(media_path: Path) -> Dict[str, Any]:
    obj = ffprobe_show_format(media_path)
    fmt = obj.get("format") or {}
    tags = fmt.get("tags") or {}
    return tags if isinstance(tags, dict) else {}


def extract_prompt_workflow_from_tags(tags: Dict[str, Any]) -> Tuple[Optional[Any], Optional[Any]]:
    """
    Extract resolved ComfyUI prompt/workflow JSON (if embedded) from container tags.
    """
    prompt_obj = None
    workflow_obj = None

    if "prompt" in tags:
        prompt_obj = maybe_json(tags.get("prompt"))
    if "workflow" in tags:
        workflow_obj = maybe_json(tags.get("workflow"))

    if prompt_obj is None or workflow_obj is None:
        for _, v in tags.items():
            obj = maybe_json(v)
            if obj is None:
                continue
            # Some muxers (or postprocessors) wrap prompt/workflow under a single tag
            # like `comment`:
            #   {"prompt":"\"{...}\"","workflow":"\"{...}\""}
            # Handle that wrapper explicitly.
            if isinstance(obj, dict):
                if prompt_obj is None and "prompt" in obj:
                    pv = obj.get("prompt")
                    prompt_obj = pv if isinstance(pv, (dict, list)) else maybe_json(pv)
                if workflow_obj is None and "workflow" in obj:
                    wv = obj.get("workflow")
                    workflow_obj = wv if isinstance(wv, (dict, list)) else maybe_json(wv)
                if prompt_obj is not None and workflow_obj is not None:
                    break
            if prompt_obj is None and isinstance(obj, dict) and obj:
                any_node = next(iter(obj.values()))
                if isinstance(any_node, dict) and ("class_type" in any_node or "inputs" in any_node):
                    prompt_obj = obj
            if workflow_obj is None and isinstance(obj, dict):
                if "nodes" in obj and "links" in obj:
                    workflow_obj = obj
            if prompt_obj is not None and workflow_obj is not None:
                break

    return prompt_obj, workflow_obj


def extract_prompt_workflow_from_png_chunks(chunks: Dict[str, str]) -> Tuple[Optional[Any], Optional[Any]]:
    prompt_obj = maybe_json(chunks.get("prompt"))
    workflow_obj = maybe_json(chunks.get("workflow"))
    return prompt_obj, workflow_obj


def stable_json_sha256(obj: Any) -> Optional[str]:
    try:
        s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return None
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def json_min(obj: Any) -> Optional[str]:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return None


def _coerce_int_seed(v: Any) -> Optional[int]:
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        t = v.strip()
        if not t:
            return None
        try:
            n = int(t, 10)
            return n
        except ValueError:
            return None
    return None


def collect_seeds_from_prompt(prompt_obj: Any) -> Dict[str, Any]:
    """
    Workflow-specific heuristic:
    - Prefer RandomNoise.inputs.noise_seed (SamplerCustomAdvanced path)
    - Otherwise fall back to KSampler.inputs.seed
    - ``random_noise_nodes`` / ``ksampler_seed_nodes`` record per-node values for correlating
      with embedded PNG/MP4 ``prompt`` metadata after a run (Comfy may update widgets when
      control_after_generate is increment/randomize).
    """
    noise_seeds: set[int] = set()
    ksampler_seeds: set[int] = set()
    random_noise_nodes: List[Dict[str, Any]] = []
    ksampler_seed_nodes: List[Dict[str, Any]] = []

    if not isinstance(prompt_obj, dict):
        return {
            "used_seed": None,
            "seed_source": None,
            "noise_seeds": [],
            "ksampler_seeds": [],
            "random_noise_nodes": [],
            "ksampler_seed_nodes": [],
        }

    for nid, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        ctype = node.get("class_type")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if ctype == "RandomNoise":
            v = inputs.get("noise_seed")
            if isinstance(v, int):
                noise_seeds.add(v)
            else:
                coerced = _coerce_int_seed(v)
                if coerced is not None:
                    noise_seeds.add(coerced)
            cad = inputs.get("control_after_generate")
            random_noise_nodes.append(
                {
                    "node_id": str(nid),
                    "noise_seed": _coerce_int_seed(inputs.get("noise_seed")),
                    "control_after_generate": cad if isinstance(cad, str) else None,
                }
            )
        elif ctype in ("KSampler", "KSamplerAdvanced"):
            v = inputs.get("seed")
            if isinstance(v, int):
                ksampler_seeds.add(v)
            else:
                coerced = _coerce_int_seed(v)
                if coerced is not None:
                    ksampler_seeds.add(coerced)
            cad = inputs.get("control_after_generate")
            ksampler_seed_nodes.append(
                {
                    "node_id": str(nid),
                    "class_type": str(ctype),
                    "seed": _coerce_int_seed(inputs.get("seed")),
                    "control_after_generate": cad if isinstance(cad, str) else None,
                }
            )

    used_seed = min(noise_seeds) if noise_seeds else (min(ksampler_seeds) if ksampler_seeds else None)
    seed_source = None
    if used_seed is not None:
        if used_seed in noise_seeds:
            seed_source = "RandomNoise.inputs.noise_seed"
        elif used_seed in ksampler_seeds:
            seed_source = "KSampler.inputs.seed"

    return {
        "used_seed": used_seed,
        "seed_source": seed_source,
        "noise_seeds": sorted(noise_seeds),
        "ksampler_seeds": sorted(ksampler_seeds),
        "random_noise_nodes": random_noise_nodes,
        "ksampler_seed_nodes": ksampler_seed_nodes,
    }


def extract_preset(prompt_obj: Any) -> Optional[Dict[str, Any]]:
    """
    Build a compact preset from the resolved ComfyUI prompt JSON.
    """
    if not isinstance(prompt_obj, dict):
        return None

    preset: Dict[str, Any] = {"nodes": {}}
    KEEP: Dict[str, List[str]] = {
        "PrimitiveStringMultiline": ["value"],
        "LoadImage": ["image"],
        "RandomNoise": ["noise_seed", "control_after_generate"],
        "mxSlider": ["Xi", "Xf", "isfloatX"],
        "mxSlider2D": ["Xi", "Xf", "Yi", "Yf", "isfloatX", "isfloatY"],
        "CFGGuider": ["cfg"],
        "BasicScheduler": ["steps", "denoise", "scheduler"],
        "KSamplerSelect": ["sampler_name"],
        "VHS_VideoCombine": ["frame_rate", "filename_prefix", "format", "crf", "pix_fmt", "save_metadata"],
        "RIFE VFI": ["multiplier", "fast_mode", "ensemble", "ckpt_name"],
    }

    for node_id, node in prompt_obj.items():
        if not isinstance(node, dict):
            continue
        ctype = node.get("class_type")
        if ctype not in KEEP:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
        title = meta.get("title")
        key = f"{node_id}:{title}" if isinstance(title, str) and title else str(node_id)

        kept: Dict[str, Any] = {}
        for k in KEEP[ctype]:
            v = inputs.get(k)
            # Skip graph references like ["123",0]
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                continue
            if isinstance(v, (int, float, bool)) or v is None or isinstance(v, str):
                kept[k] = v
        if kept:
            preset["nodes"][key] = {"class_type": ctype, "inputs": kept}

    return preset

