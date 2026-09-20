#!/usr/bin/env python3
"""
Vision V1 — caption runner API (run-anywhere).

Same job contract for local transformers, ComfyUI (local Docker), or remote Comfy
(e.g. RunPod exposing :8188). Runners differ only in how they turn a JPEG into text.

  CaptionRequest  →  CaptionRunner.caption()  →  CaptionResult

Comfy path: upload (or copy into input/) → LoadImage → Florence2 → poll /history.
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable
from http_retry import http_json_with_retry, urlopen_read_with_retry

DEFAULT_COMFY_MODEL = "microsoft/Florence-2-base"
DEFAULT_COMFY_TASK = "caption"
DEFAULT_CLIENT_ID = "vision_slice_v1"

# Short aliases → Florence2Run enum values (Comfy rejects unknown task strings with HTTP 400).
COMFY_TASK_ALIASES: Dict[str, str] = {
    "tags": "prompt_gen_tags",
    "prompt_gen_tag": "prompt_gen_tags",
    "mixed": "prompt_gen_mixed_caption",
    "mixed_caption": "prompt_gen_mixed_caption",
    "mixed_plus": "prompt_gen_mixed_caption_plus",
    "mixed_caption_plus": "prompt_gen_mixed_caption_plus",
    "analyze": "prompt_gen_analyze",
}


def normalize_comfy_task(task: str) -> str:
    t = str(task or "").strip()
    if not t:
        return DEFAULT_COMFY_TASK
    return COMFY_TASK_ALIASES.get(t, t)


@dataclass
class CaptionRequest:
    image_path: Path
    asset_relpath: str = ""
    frame_relpath: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CaptionResult:
    caption: str
    provider: str
    model_pin: str
    runner: str
    raw: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class CaptionRunner(Protocol):
    """Portable caption backend. Construct once; call caption() many times."""

    def caption(self, req: CaptionRequest) -> CaptionResult: ...

    def close(self) -> None: ...


def _http_json(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    timeout_s: float = 60,
) -> Any:
    return http_json_with_retry(method=method, url=url, payload=payload, timeout_s=timeout_s)


def _http_upload_image(
    server: str,
    image_path: Path,
    *,
    subfolder: str = "vision_v1",
    overwrite: bool = True,
    timeout_s: float = 120,
) -> Dict[str, Any]:
    """
    POST multipart to Comfy ``/upload/image`` (same API on RunPod-exposed Comfy).

    Returns Comfy's JSON: name, subfolder, type.
    """
    server = server.rstrip("/")
    boundary = f"----VisionSlice{uuid.uuid4().hex}"
    filename = image_path.name
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")

    add_field("overwrite", "true" if overwrite else "false")
    if subfolder:
        add_field("subfolder", subfolder)
    add_field("type", "input")

    file_bytes = image_path.read_bytes()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode()
    )
    body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
    body.extend(file_bytes)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    raw = urlopen_read_with_retry(
        method="POST",
        url=f"{server}/upload/image",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout_s=timeout_s,
        retry_attempts=2,
        retry_backoff_s=0.35,
    )
    return json.loads(raw.decode("utf-8", "replace"))


def build_florence_caption_prompt(
    *,
    image_name: Optional[str] = None,
    image_names: Optional[Sequence[str]] = None,
    model: str = DEFAULT_COMFY_MODEL,
    task: str = DEFAULT_COMFY_TASK,
    precision: str = "fp16",
    attention: str = "sdpa",
    keep_model_loaded: bool = True,
    max_new_tokens: int = 64,
    num_beams: int = 3,
    do_sample: bool = False,
    seed: int = 1,
    scale_size: int = 768,
) -> Dict[str, Any]:
    """
    LoadImage → Florence load → Florence2Run → ShowText (caption sink).

    One image keeps the original 4-node graph. Several images are scaled to a
    common square, ImageBatch'd, and run through one Florence2Run (the node
    loops ``generate`` per batch element).
    """
    task = normalize_comfy_task(task)
    names = [str(n).strip() for n in (image_names or []) if str(n).strip()]
    if not names and image_name:
        names = [str(image_name).strip()]
    if not names:
        raise ValueError("image_name or image_names required")
    model_node = {
        "class_type": "DownloadAndLoadFlorence2Model",
        "inputs": {
            "model": model,
            "precision": precision,
            "attention": attention,
            "convert_to_safetensors": False,
        },
    }
    run_inputs = {
        "florence2_model": ["2", 0],
        "text_input": "",
        "task": task,
        "fill_mask": True,
        "keep_model_loaded": keep_model_loaded,
        "max_new_tokens": int(max_new_tokens),
        "num_beams": int(num_beams),
        "do_sample": bool(do_sample),
        "output_mask_select": "",
        "seed": int(seed),
    }
    if len(names) == 1:
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": names[0]}},
            "2": model_node,
            "3": {"class_type": "Florence2Run", "inputs": {**run_inputs, "image": ["1", 0]}},
            "4": {"class_type": "ShowText|pysssss", "inputs": {"text": ["3", 2]}},
        }
    prompt: Dict[str, Any] = {"2": model_node}
    scaled_ids: List[str] = []
    side = max(1, int(scale_size or 768))
    for i, name in enumerate(names):
        lid = str(10 + i)
        sid = str(40 + i)
        prompt[lid] = {"class_type": "LoadImage", "inputs": {"image": name}}
        prompt[sid] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": [lid, 0],
                "upscale_method": "lanczos",
                "width": side,
                "height": side,
                "crop": "center",
            },
        }
        scaled_ids.append(sid)
    batched = scaled_ids[0]
    for j, sid in enumerate(scaled_ids[1:], start=1):
        bid = str(70 + j)
        prompt[bid] = {
            "class_type": "ImageBatch",
            "inputs": {"image1": [batched, 0], "image2": [sid, 0]},
        }
        batched = bid
    prompt["3"] = {"class_type": "Florence2Run", "inputs": {**run_inputs, "image": [batched, 0]}}
    prompt["4"] = {"class_type": "ShowText|pysssss", "inputs": {"text": ["3", 2]}}
    return prompt


def extract_caption_from_history(history_entry: Dict[str, Any], *, run_node_id: str = "3") -> str:
    """Pull caption STRING from ShowText sink and/or Florence2Run in /history."""
    texts = extract_captions_from_history(history_entry, expected=1, run_node_id=run_node_id)
    if not texts:
        raise RuntimeError("could not parse caption from history outputs")
    return texts[0]


def _coerce_text_list(val: Any) -> List[str]:
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        return [s] if s else []
    if isinstance(val, list):
        out: List[str] = []
        for x in val:
            out.extend(_coerce_text_list(x))
        return out
    s = str(val).strip()
    return [s] if s else []


def extract_captions_from_history(
    history_entry: Dict[str, Any],
    *,
    expected: int = 1,
    run_node_id: str = "3",
) -> List[str]:
    outputs = history_entry.get("outputs") if isinstance(history_entry, dict) else None
    if not isinstance(outputs, dict):
        raise RuntimeError("history entry missing outputs")
    want = max(1, int(expected or 1))
    # Batched Florence STRING is on the run node; ShowText often joins the list.
    prefer = [str(run_node_id), "4"] if want > 1 else ["4", str(run_node_id)]
    order = [*prefer, run_node_id, *list(outputs.keys())]
    seen = set()
    best: List[str] = []
    for nid in order:
        if nid in seen:
            continue
        seen.add(nid)
        node_out = outputs.get(str(nid))
        if not isinstance(node_out, dict):
            continue
        for key in ("text", "string", "caption"):
            texts = _coerce_text_list(node_out.get(key))
            if len(texts) >= want:
                return texts[:want]
            if len(texts) > len(best):
                best = texts
    if want > 1 and len(best) == 1 and "\n" in best[0]:
        lines = [ln.strip() for ln in best[0].splitlines() if ln.strip()]
        if len(lines) >= want:
            return lines[:want]
    if want == 1 and best:
        return best[:1]
    raise RuntimeError(
        f"could not parse {want} caption(s) from history outputs keys={list(outputs.keys())} got={len(best)}"
    )


@dataclass
class ComfyRunnerConfig:
    """
    HTTP Comfy endpoint — works for docker-compose :8188 and RunPod port mapping alike.

    image_mode:
      - upload: POST /upload/image (works when runner FS ≠ Comfy FS; preferred for RunPod)
      - input_copy: copy into comfy_input_root/subfolder (shared bind mounts)
      - input_ref: LoadImage the file already under Comfy's input bind (no copy/upload)
    """

    server: str = "http://127.0.0.1:8188"
    model: str = DEFAULT_COMFY_MODEL
    task: str = DEFAULT_COMFY_TASK
    client_id: str = DEFAULT_CLIENT_ID
    runner_label: str = "comfy"  # comfy | runpod | docker
    image_mode: str = "upload"  # upload | input_copy | input_ref
    input_subdir: str = "vision_v1"
    comfy_input_root: Optional[Path] = None  # required for input_copy
    keep_model_loaded: bool = True
    precision: str = "fp16"
    attention: str = "sdpa"
    max_new_tokens: int = 64
    num_beams: int = 3
    do_sample: bool = False
    seed: int = 1
    timeout_s: float = 900.0
    poll_interval_s: float = 0.15
    submit_timeout_s: float = 60.0
    front: bool = False


def _comfy_input_load_name(
    image_path: Path,
    *,
    relpath: str = "",
    extra_root: Optional[Path] = None,
) -> Optional[str]:
    """LoadImage name if *image_path* is under a Comfy input bind; else None.

    Factory outputs (``output/og/…``) are not in ``input/``. Pretending they are
    makes Comfy return HTTP 400 from LoadImage.
    """
    roots: List[Path] = []
    if extra_root is not None:
        roots.append(Path(extra_root))
    env_root = os.environ.get("COMFYUI_BIND_INPUT_DIR", "").strip()
    if env_root:
        roots.append(Path(env_root))
    roots.extend([Path("/ComfyUI/input"), Path("/workspace/input")])
    raw = str(relpath or "").replace("\\", "/").strip().lstrip("/")
    if raw.lower().startswith("input/"):
        raw = raw[6:]
    try:
        resolved = image_path.expanduser().resolve()
        for root in roots:
            try:
                return resolved.relative_to(Path(root).expanduser().resolve()).as_posix()
            except (ValueError, OSError):
                continue
        if raw:
            for root in roots:
                try:
                    base = Path(root).expanduser().resolve()
                    for cand in (base / raw, base / Path(raw).name):
                        if cand.is_file():
                            return cand.relative_to(base).as_posix()
                except (ValueError, OSError):
                    continue
    except OSError:
        pass
    return None


def history_execution_error(entry: Any) -> Optional[str]:
    """If Comfy already finished this prompt with an error, return a short reason.

    ``/history`` entries for failed LoadImage jobs often have ``status_str=error``
    and empty ``outputs``. Waiting for outputs then burns the full timeout.
    """
    if not isinstance(entry, dict):
        return None
    status = entry.get("status")
    if not isinstance(status, dict):
        return None
    status_str = str(status.get("status_str") or "").strip().lower()
    msgs = status.get("messages") or []
    err_txt = ""
    interrupted = False
    if isinstance(msgs, list):
        for m in msgs:
            if not (isinstance(m, (list, tuple)) and m):
                continue
            kind = str(m[0] or "")
            payload = m[1] if len(m) > 1 and isinstance(m[1], dict) else {}
            if kind == "execution_error":
                node = payload.get("node_type") or payload.get("node_id") or "node"
                ex = payload.get("exception_message") or payload.get("exception_type") or "error"
                err_txt = f"{node}: {ex}"
            elif kind == "execution_interrupted":
                interrupted = True
    if err_txt:
        return err_txt
    if status_str == "error":
        return "Comfy execution error"
    if status_str == "interrupted" or interrupted:
        return "Comfy execution interrupted"
    return None


class ComfyCaptionRunner:
    """Caption stills via ComfyUI Florence2 nodes over HTTP."""

    def __init__(self, cfg: ComfyRunnerConfig) -> None:
        self.cfg = cfg
        self.server = cfg.server.rstrip("/")
        self.last_prompt_id: Optional[str] = None

    def close(self) -> None:
        return None

    def _upload_image_ref(self, image_path: Path) -> str:
        cfg = self.cfg
        up = _http_upload_image(
            self.server,
            image_path,
            subfolder=cfg.input_subdir,
            timeout_s=cfg.submit_timeout_s,
        )
        name = str(up.get("name") or image_path.name)
        sub = str(up.get("subfolder") or cfg.input_subdir or "").strip().strip("/")
        return f"{sub}/{name}" if sub else name

    def _image_ref_for_load_image(self, image_path: Path, *, relpath: str = "") -> str:
        cfg = self.cfg
        if cfg.image_mode == "upload":
            return self._upload_image_ref(image_path)

        if cfg.image_mode == "input_copy":
            if not cfg.comfy_input_root:
                raise ValueError("comfy_input_root required for image_mode=input_copy")
            dest_dir = Path(cfg.comfy_input_root) / cfg.input_subdir
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / image_path.name
            if dest.resolve() != image_path.resolve():
                shutil.copy2(image_path, dest)
            return f"{cfg.input_subdir}/{image_path.name}".replace("\\", "/")

        if cfg.image_mode == "input_ref":
            ref = _comfy_input_load_name(image_path, relpath=relpath, extra_root=cfg.comfy_input_root)
            if ref:
                return ref
            # Output-tree / recovered stills: file is real, but LoadImage cannot see it.
            return self._upload_image_ref(image_path)

        raise ValueError(f"unknown image_mode: {cfg.image_mode}")

    def _wait_history(self, prompt_id: str) -> Dict[str, Any]:
        deadline = time.time() + float(self.cfg.timeout_s)
        url = f"{self.server}/history/{urllib.parse.quote(prompt_id)}"
        last_err: Optional[Exception] = None
        while time.time() < deadline:
            try:
                doc = _http_json("GET", url, timeout_s=min(30.0, self.cfg.submit_timeout_s))
                if isinstance(doc, dict) and prompt_id in doc:
                    entry = doc[prompt_id]
                    fail = history_execution_error(entry)
                    if fail:
                        raise RuntimeError(fail)
                    if isinstance(entry, dict) and entry.get("outputs"):
                        return entry
            except RuntimeError:
                raise
            except Exception as e:
                last_err = e
            time.sleep(float(self.cfg.poll_interval_s))
        raise TimeoutError(
            f"Comfy history timeout for {prompt_id} after {self.cfg.timeout_s}s"
            + (f" last_err={last_err}" if last_err else "")
        )

    def caption(self, req: CaptionRequest) -> CaptionResult:
        return self.caption_many([req])[0]

    def caption_many(self, reqs: Sequence[CaptionRequest]) -> List[CaptionResult]:
        items = list(reqs)
        if not items:
            return []
        t0 = time.perf_counter()
        refs: List[str] = []
        for req in items:
            image_path = Path(req.image_path)
            if not image_path.is_file() or image_path.stat().st_size == 0:
                raise FileNotFoundError(f"missing/empty frame: {image_path}")
            refs.append(
                self._image_ref_for_load_image(image_path, relpath=req.asset_relpath)
            )
        t_upload = time.perf_counter()
        prompt = build_florence_caption_prompt(
            image_names=refs,
            model=self.cfg.model,
            task=self.cfg.task,
            precision=self.cfg.precision,
            attention=self.cfg.attention,
            keep_model_loaded=self.cfg.keep_model_loaded,
            max_new_tokens=self.cfg.max_new_tokens,
            num_beams=self.cfg.num_beams,
            do_sample=self.cfg.do_sample,
            seed=self.cfg.seed,
        )
        payload: Dict[str, Any] = {"prompt": prompt, "client_id": self.cfg.client_id}
        if self.cfg.front:
            payload["front"] = True
        submit = _http_json(
            "POST",
            f"{self.server}/prompt",
            payload,
            timeout_s=self.cfg.submit_timeout_s,
        )
        t_submit = time.perf_counter()
        prompt_id = submit.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise RuntimeError(f"Comfy submit missing prompt_id: {submit}")
        self.last_prompt_id = prompt_id

        entry = self._wait_history(prompt_id)
        t_done = time.perf_counter()
        captions = extract_captions_from_history(entry, expected=len(refs), run_node_id="3")
        timing = {
            "upload_s": round(t_upload - t0, 3),
            "submit_s": round(t_submit - t_upload, 3),
            "wait_s": round(t_done - t_submit, 3),
            "total_s": round(t_done - t0, 3),
            "batch": len(refs),
        }
        out: List[CaptionResult] = []
        for req, image_ref, caption in zip(items, refs, captions):
            out.append(
                CaptionResult(
                    caption=caption,
                    provider="comfy_florence2",
                    model_pin=self.cfg.model,
                    runner=self.cfg.runner_label,
                    raw={
                        "prompt_id": prompt_id,
                        "image_ref": image_ref,
                        "server": self.server,
                        "task": self.cfg.task,
                        "timing": timing,
                    },
                )
            )
        return out


class DryRunCaptionRunner:
    def __init__(self, *, runner_label: str = "local") -> None:
        self.runner_label = runner_label

    def close(self) -> None:
        return None

    def caption(self, req: CaptionRequest) -> CaptionResult:
        t0 = time.perf_counter()
        meta = req.meta or {}
        slice_kind = meta.get("slice") or "window"
        t0m = meta.get("t0")
        t1m = meta.get("t1")
        rel = req.asset_relpath or req.image_path.name
        text = f"[dry-run] {slice_kind} {t0m}-{t1m}s of {rel}"
        return CaptionResult(
            caption=text,
            provider="dry-run",
            model_pin="dry-run",
            runner=self.runner_label,
            raw={"timing": {"total_s": round(time.perf_counter() - t0, 3)}},
        )

    def caption_many(self, reqs: Sequence[CaptionRequest]) -> List[CaptionResult]:
        return [self.caption(r) for r in reqs]


def make_runner(
    *,
    provider: str,
    runner_label: str = "local",
    comfy_server: str = "http://127.0.0.1:8188",
    model_pin: str = DEFAULT_COMFY_MODEL,
    device: str = "cuda",
    image_mode: str = "upload",
    comfy_input_root: Optional[Path] = None,
    dry_run: bool = False,
    task: str = DEFAULT_COMFY_TASK,
    max_new_tokens: int = 64,
    front: bool = False,
    poll_interval_s: float = 0.15,
) -> CaptionRunner:
    """
    Factory used by vision_slice_caption_run.

    provider:
      - dry-run / dry_run
      - comfy | runpod | comfyui → ComfyCaptionRunner (URL = local Docker or RunPod :8188)
      - transformers | florence2 → in-process Florence (optional deps)
    """
    if dry_run or provider in ("dry-run", "dry_run"):
        return DryRunCaptionRunner(runner_label=runner_label)

    if provider in ("comfy", "runpod", "comfyui"):
        label = runner_label if runner_label not in ("local", "") else provider
        return ComfyCaptionRunner(
            ComfyRunnerConfig(
                server=comfy_server,
                model=model_pin,
                task=normalize_comfy_task(task),
                max_new_tokens=max_new_tokens,
                runner_label=label,
                image_mode=image_mode,
                comfy_input_root=comfy_input_root,
                front=bool(front),
                poll_interval_s=float(poll_interval_s),
            )
        )

    if provider in ("transformers", "florence2", "local"):
        return TransformersFlorenceRunner(
            model_pin=model_pin, device=device, runner_label=runner_label
        )

    raise ValueError(f"unknown provider: {provider}")


class TransformersFlorenceRunner:
    """In-process Florence-2 (optional torch/transformers)."""

    def __init__(self, *, model_pin: str, device: str, runner_label: str = "local") -> None:
        self.model_pin = model_pin
        self.device = device
        self.runner_label = runner_label
        self._model = None
        self._processor = None
        self._torch = None
        self._Image = None
        self._model_load_s: Optional[float] = None

    def close(self) -> None:
        return None

    def _ensure(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from PIL import Image
            from transformers import AutoModelForCausalLM, AutoProcessor
        except ImportError as e:
            raise RuntimeError(
                "Florence captioning requires torch, transformers, and Pillow. "
                "Prefer --provider comfy against a running ComfyUI, or use --dry-run."
            ) from e
        t0 = time.perf_counter()
        self._processor = AutoProcessor.from_pretrained(self.model_pin, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_pin, trust_remote_code=True, torch_dtype="auto"
        )
        if self.device.startswith("cuda"):
            self._model = self._model.to(self.device)
        self._model.eval()
        self._torch = torch
        self._Image = Image
        self._model_load_s = time.perf_counter() - t0

    def caption(self, req: CaptionRequest) -> CaptionResult:
        t0 = time.perf_counter()
        load_s = self._model_load_s
        self._ensure()
        # First _ensure sets load time; capture for this result only once.
        just_loaded = load_s is None and self._model_load_s is not None
        assert self._model is not None and self._processor is not None and self._Image is not None
        image = self._Image.open(req.image_path).convert("RGB")
        task = "<CAPTION>"
        inputs = self._processor(text=task, images=image, return_tensors="pt")
        if self.device.startswith("cuda"):
            inputs = {k: v.to(self.device) if hasattr(v, "to") else v for k, v in inputs.items()}
        with self._torch.no_grad():
            generated = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs.get("pixel_values"),
                max_new_tokens=64,
                num_beams=1,
                do_sample=False,
            )
        text = self._processor.batch_decode(generated, skip_special_tokens=False)[0]
        parsed = self._processor.post_process_generation(
            text, task=task, image_size=(image.width, image.height)
        )
        if isinstance(parsed, dict):
            cap = parsed.get(task) or parsed.get("<CAPTION>") or next(iter(parsed.values()), "")
            caption = str(cap).strip()
        else:
            caption = str(parsed).strip()
        t_done = time.perf_counter()
        timing: Dict[str, Any] = {"total_s": round(t_done - t0, 3)}
        if just_loaded and self._model_load_s is not None:
            timing["model_load_s"] = round(self._model_load_s, 3)
            timing["inference_s"] = round(max(0.0, (t_done - t0) - self._model_load_s), 3)
        return CaptionResult(
            caption=caption,
            provider="florence2",
            model_pin=self.model_pin,
            runner=self.runner_label,
            raw={"timing": timing},
        )
