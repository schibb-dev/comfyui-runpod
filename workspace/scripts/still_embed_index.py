#!/usr/bin/env python3
"""Input-still CLIP embedding index (sqlite primary, optional JSON export).

Vectors are packed as little-endian float32. Cosine search uses the stdlib so
the index works without numpy/torch. Encoding (backfill) is a swappable encoder
selected by ``STILL_CLIP_ENCODER`` (auto | comfy | deterministic).
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DB_BASENAME = "still_clip_embeddings.sqlite"
EXPORT_DIRNAME = "clip_embeddings_export"
SCHEMA_VERSION = "1"

_SHA256_RE = __import__("re").compile(r"([0-9a-f]{64})", __import__("re").IGNORECASE)


def extract_content_id(path: str) -> Optional[str]:
    m = _SHA256_RE.search(Path(str(path or "")).name)
    return m.group(1).lower() if m else None


def default_db_path(*, data_root: Optional[Path] = None) -> Path:
    env = os.environ.get("STILL_CLIP_DB_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if data_root is None:
        repo = Path(__file__).resolve().parents[2]
        data_root = Path(os.environ.get("SHAPE_FACTORY_DATA_ROOT") or (repo / ".data"))
    return Path(data_root).expanduser().resolve() / "shape_factory" / DB_BASENAME


def default_export_dir(*, data_root: Optional[Path] = None) -> Path:
    env = os.environ.get("STILL_CLIP_EXPORT_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if data_root is None:
        repo = Path(__file__).resolve().parents[2]
        data_root = Path(os.environ.get("SHAPE_FACTORY_DATA_ROOT") or (repo / ".data"))
    return Path(data_root).expanduser().resolve() / "shape_factory" / EXPORT_DIRNAME


def pack_vector(values: Sequence[float]) -> bytes:
    return array.array("f", [float(x) for x in values]).tobytes()


def unpack_vector(blob: bytes) -> List[float]:
    a = array.array("f")
    a.frombytes(bytes(blob or b""))
    return list(a)


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n <= 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = float(a[i])
        y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 1e-18 or nb <= 1e-18:
        return 0.0
    return float(dot / math.sqrt(na * nb))


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embeddings (
          content_id TEXT PRIMARY KEY,
          model_id TEXT NOT NULL,
          dim INTEGER NOT NULL,
          vector BLOB NOT NULL,
          source_path TEXT,
          updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model_id);
        """
    )
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", SCHEMA_VERSION),
    )
    con.commit()


def ensure_db(db_path: Path) -> Path:
    con = connect(db_path)
    try:
        init_db(con)
    finally:
        con.close()
    return db_path


class SqliteEmbedIndex:
    """EmbedIndex protocol implementation."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def get(self, content_id: str) -> Optional[Dict[str, Any]]:
        cid = str(content_id or "").strip().lower()
        if not cid:
            return None
        ensure_db(self.db_path)
        con = connect(self.db_path)
        try:
            row = con.execute("SELECT * FROM embeddings WHERE content_id=?", (cid,)).fetchone()
        finally:
            con.close()
        if not row:
            return None
        return _row_to_dict(row)

    def upsert(
        self,
        content_id: str,
        vector: Sequence[float],
        *,
        model_id: str,
        source_path: str = "",
    ) -> None:
        cid = str(content_id or "").strip().lower()
        if not cid:
            raise ValueError("missing_content_id")
        vals = [float(x) for x in vector]
        if not vals:
            raise ValueError("empty_vector")
        blob = pack_vector(vals)
        ensure_db(self.db_path)
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO embeddings(content_id, model_id, dim, vector, source_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_id) DO UPDATE SET
                  model_id=excluded.model_id,
                  dim=excluded.dim,
                  vector=excluded.vector,
                  source_path=excluded.source_path,
                  updated_at=excluded.updated_at
                """,
                (cid, str(model_id or "").strip() or "unknown", len(vals), blob, str(source_path or ""), time.time()),
            )
            con.commit()
        finally:
            con.close()

    def upsert_many(
        self,
        rows: Sequence[Tuple[str, Sequence[float], str, str]],
    ) -> int:
        """Batch-write (content_id, vector, model_id, source_path). Returns written count."""
        now = time.time()
        packed: List[Tuple[str, str, int, bytes, str, float]] = []
        for content_id, vector, model_id, source_path in rows:
            cid = str(content_id or "").strip().lower()
            if not cid:
                continue
            vals = [float(x) for x in vector]
            if not vals:
                continue
            packed.append(
                (
                    cid,
                    str(model_id or "").strip() or "unknown",
                    len(vals),
                    pack_vector(vals),
                    str(source_path or ""),
                    now,
                )
            )
        if not packed:
            return 0
        ensure_db(self.db_path)
        con = connect(self.db_path)
        try:
            con.executemany(
                """
                INSERT INTO embeddings(content_id, model_id, dim, vector, source_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_id) DO UPDATE SET
                  model_id=excluded.model_id,
                  dim=excluded.dim,
                  vector=excluded.vector,
                  source_path=excluded.source_path,
                  updated_at=excluded.updated_at
                """,
                packed,
            )
            con.commit()
        finally:
            con.close()
        return len(packed)

    def nearest(self, content_id: str, *, limit: int = 24) -> List[Dict[str, Any]]:
        cid = str(content_id or "").strip().lower()
        query = self.get(cid)
        if not query:
            return []
        qvec = query["vector"]
        qmodel = str(query.get("model_id") or "")
        lim = max(1, min(200, int(limit or 24)))
        con = connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT content_id, model_id, dim, vector, source_path FROM embeddings WHERE content_id != ?",
                (cid,),
            ).fetchall()
        finally:
            con.close()
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for row in rows:
            if qmodel and str(row["model_id"] or "") != qmodel:
                continue
            vec = unpack_vector(row["vector"])
            score = cosine_similarity(qvec, vec)
            scored.append(
                (
                    score,
                    {
                        "content_id": row["content_id"],
                        "score": score,
                        "model_id": row["model_id"],
                        "source_path": row["source_path"],
                    },
                )
            )
        scored.sort(key=lambda x: (-x[0], x[1]["content_id"]))
        return [item for _, item in scored[:lim]]

    def stats(self) -> Dict[str, Any]:
        ensure_db(self.db_path)
        con = connect(self.db_path)
        try:
            count = int(con.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
            models = [
                {"model_id": r["model_id"], "count": int(r["n"])}
                for r in con.execute(
                    "SELECT model_id, COUNT(*) AS n FROM embeddings GROUP BY model_id ORDER BY n DESC"
                )
            ]
        finally:
            con.close()
        return {"ok": True, "path": str(self.db_path), "count": count, "models": models}


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "content_id": row["content_id"],
        "model_id": row["model_id"],
        "dim": int(row["dim"]),
        "vector": unpack_vector(row["vector"]),
        "source_path": row["source_path"],
        "updated_at": float(row["updated_at"] or 0.0),
    }


def export_json(content_id: str, row: Dict[str, Any], *, export_dir: Path) -> Path:
    export_dir.mkdir(parents=True, exist_ok=True)
    cid = str(content_id).strip().lower()
    path = export_dir / f"{cid}.json"
    doc = {
        "content_id": cid,
        "model_id": row.get("model_id"),
        "dim": row.get("dim") or len(row.get("vector") or []),
        "vector": list(row.get("vector") or []),
        "source_path": row.get("source_path") or "",
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def encode_still_image(path: Path) -> Tuple[List[float], str]:
    """Return (vector, model_id). Encoder selected by STILL_CLIP_ENCODER."""
    mode = os.environ.get("STILL_CLIP_ENCODER", "auto").strip().lower() or "auto"
    if mode == "deterministic":
        return _encode_deterministic(path)
    if mode == "comfy":
        return _encode_comfy(path)
    if mode == "auto":
        try:
            return _encode_comfy(path)
        except Exception:
            return _encode_deterministic(path)
    raise ValueError(f"unknown STILL_CLIP_ENCODER: {mode}")


def _encode_deterministic(path: Path) -> Tuple[List[float], str]:
    """Stdlib encoder for tests / bootstrap when CLIP isn't loaded.

    Not a CLIP model — provenance records ``deterministic-sha256-32``.
    """
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).digest()
    vec = [(b / 127.5) - 1.0 for b in digest]
    return vec, "deterministic-sha256-32"


_COMFY_CLIP: Optional[Tuple[Any, str]] = None
_ENCODE_COUNT = 0
_DECODE_POOL: Optional[ThreadPoolExecutor] = None


def _decode_pool() -> ThreadPoolExecutor:
    global _DECODE_POOL
    if _DECODE_POOL is None:
        n = max(1, int(os.environ.get("STILL_CLIP_DECODE_WORKERS", "16") or "16"))
        _DECODE_POOL = ThreadPoolExecutor(max_workers=n, thread_name_prefix="still-clip-dec")
    return _DECODE_POOL


def _pick_clip_vision_name(names: Sequence[str], want: str) -> str:
    if want:
        return want
    usable = [
        n
        for n in names
        if str(n).lower().endswith(".safetensors") and ".partial" not in str(n).lower()
    ]
    if usable:
        return usable[0]
    if names:
        return names[0]
    raise FileNotFoundError("no clip_vision models in ComfyUI folder_paths")


def _comfy_clip_vision() -> Tuple[Any, str]:
    """Load clip_vision once per process (backfill encodes thousands of stills)."""
    global _COMFY_CLIP
    if _COMFY_CLIP is not None:
        return _COMFY_CLIP
    comfy_root = os.environ.get("COMFYUI_ROOT", "/ComfyUI").strip() or "/ComfyUI"
    if comfy_root not in sys.path:
        sys.path.insert(0, comfy_root)
    import folder_paths  # type: ignore
    from comfy.clip_vision import load as load_clip_vision  # type: ignore
    import comfy.model_management as mm  # type: ignore

    want = os.environ.get("STILL_CLIP_VISION_FILE", "").strip()
    names = list(folder_paths.get_filename_list("clip_vision") or [])
    pick = _pick_clip_vision_name(names, want)
    full = folder_paths.get_full_path("clip_vision", pick)
    if not full:
        raise FileNotFoundError(f"clip_vision model not found: {pick}")
    model = load_clip_vision(full)
    mm.load_model_gpu(model.patcher)
    model_id = f"comfy-clip_vision:{Path(str(full)).name}"
    _COMFY_CLIP = (model, model_id)
    print(f"[still-embed] loaded {model_id}", file=sys.stderr, flush=True)
    return _COMFY_CLIP


def _pil_center_square(img, size: int):
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = img.crop((left, top, left + side, top + side))
    if cropped.size != (size, size):
        cropped = cropped.resize((size, size), resample=3)  # BICUBIC
    return cropped


def _image_to_bhwc(path: Path, *, size: int = 224):
    import torch  # type: ignore
    from PIL import Image  # type: ignore

    img = Image.open(path)
    # JPEG can decode at 1/2, 1/4, 1/8 DCT scale — much faster than full-res then downsample.
    # size*4 (~896 for CLIP-224) keeps 1/2 DCT on typical 1k stills instead of collapsing to 1/4.
    try:
        img.draft("RGB", (int(size) * 4, int(size) * 4))
    except Exception:
        pass
    img = img.convert("RGB")
    img = _pil_center_square(img, int(size))
    try:
        import numpy as np  # type: ignore

        arr = torch.from_numpy(np.asarray(img).copy()).float().div_(255.0)
    except Exception:
        arr = torch.tensor(list(img.getdata()), dtype=torch.float32).view(img.size[1], img.size[0], 3) / 255.0
    return arr.unsqueeze(0)


def _cuda_mem_mib() -> str:
    try:
        import torch  # type: ignore

        if not torch.cuda.is_available():
            return ""
        alloc = torch.cuda.memory_allocated() / (1024 * 1024)
        peak = torch.cuda.max_memory_allocated() / (1024 * 1024)
        reserved = torch.cuda.memory_reserved() / (1024 * 1024)
        return f" cuda_alloc={alloc:.0f}MiB peak={peak:.0f}MiB reserved={reserved:.0f}MiB"
    except Exception:
        return ""


def _short_exc(exc: BaseException) -> str:
    text = str(exc).replace("\n", " ")
    if "CUDA out of memory" in text or "out of memory" in text.lower():
        return "CUDA out of memory"
    return text[:240]


def _clip_batch_size() -> int:
    raw = os.environ.get("STILL_CLIP_BATCH", "16").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 16
    return max(1, min(64, n))


def _decode_still_hwc(path: Path, *, size: int):
    tensor = _image_to_bhwc(path, size=size)
    return tensor.squeeze(0)


def _encode_comfy_bhwc(batched):
    """batched: [B,H,W,3] float 0-1 on CPU. Returns list of vectors."""
    global _ENCODE_COUNT
    import torch  # type: ignore

    model, model_id = _comfy_clip_vision()
    import comfy.clip_model  # type: ignore  # noqa: E402

    size = int(getattr(model, "image_size", 224) or 224)
    mean = getattr(model, "image_mean", None) or [0.48145466, 0.4578275, 0.40821073]
    std = getattr(model, "image_std", None) or [0.26862954, 0.26130258, 0.27577711]
    device = getattr(model, "load_device", None)
    with torch.inference_mode():
        pixels = comfy.clip_model.clip_preprocess(
            batched.to(device, non_blocking=True) if device is not None else batched,
            size=size,
            mean=mean,
            std=std,
            crop=False,
        ).float()
        raw = model.model(pixel_values=pixels, intermediate_output=-2)
        embeds = raw[2].detach().float().cpu()
        del pixels, raw
    vecs = [embeds[i].reshape(-1).tolist() for i in range(int(embeds.shape[0]))]
    del embeds, batched
    _ENCODE_COUNT += len(vecs)
    if _ENCODE_COUNT <= 32 or _ENCODE_COUNT % 64 == 0:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    return vecs, model_id


def _encode_comfy(path: Path) -> Tuple[List[float], str]:
    """Encode with ComfyUI clip_vision (first available model, or STILL_CLIP_VISION_FILE)."""
    import torch  # type: ignore

    model, model_id = _comfy_clip_vision()
    size = int(getattr(model, "image_size", 224) or 224)
    batched = _image_to_bhwc(path, size=size)
    vecs, model_id = _encode_comfy_bhwc(batched)
    return vecs[0], model_id


def _decode_comfy_batch(
    items: Sequence[Tuple[str, Path]],
    *,
    size: int,
) -> Tuple[List[Optional[Any]], List[Optional[str]]]:
    decoded: List[Optional[Any]] = [None] * len(items)
    errors: List[Optional[str]] = [None] * len(items)
    if not items:
        return decoded, errors

    def _safe(i: int, path: Path):
        try:
            return i, _decode_still_hwc(path, size=size), None
        except Exception as exc:
            return i, None, _short_exc(exc)

    pool = _decode_pool()
    futs = [pool.submit(_safe, i, path) for i, (_cid, path) in enumerate(items)]
    for fut in as_completed(futs):
        i, tensor, err = fut.result()
        decoded[i] = tensor
        errors[i] = err
    return decoded, errors


def _encode_decoded_batch(
    items: Sequence[Tuple[str, Path]],
    decoded: Sequence[Optional[Any]],
    errors: Sequence[Optional[str]],
    *,
    model_id: str,
) -> List[Tuple[str, Path, Optional[List[float]], str, Optional[str]]]:
    import torch  # type: ignore

    out: List[Tuple[str, Path, Optional[List[float]], str, Optional[str]]] = [
        (cid, path, None, model_id, errors[i]) for i, (cid, path) in enumerate(items)
    ]
    ok_idx = [i for i, t in enumerate(decoded) if t is not None]
    if not ok_idx:
        return out
    batched = torch.stack([decoded[i] for i in ok_idx], dim=0)
    try:
        batched = batched.pin_memory()
    except Exception:
        pass
    vecs, model_id = _encode_comfy_bhwc(batched)
    for j, i in enumerate(ok_idx):
        cid, path = items[i]
        out[i] = (cid, path, vecs[j], model_id, None)
    return out


def _encode_comfy_paths(items: Sequence[Tuple[str, Path]]) -> List[Tuple[str, Path, Optional[List[float]], str, Optional[str]]]:
    """Return (cid, path, vector|None, model_id, error|None) for a batch of stills."""
    if not items:
        return []
    model, model_id = _comfy_clip_vision()
    size = int(getattr(model, "image_size", 224) or 224)
    decoded, errors = _decode_comfy_batch(items, size=size)
    return _encode_decoded_batch(items, decoded, errors, model_id=model_id)


def _iter_catalog_stills(*, data_root: Path) -> Iterable[Tuple[str, Path]]:
    from input_still_catalog import default_catalog_path, default_input_root, resolve_catalog_still_path

    cat = default_catalog_path(data_root=data_root)
    if not cat.is_file():
        return
    root = default_input_root()
    con = sqlite3.connect(str(cat))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT path FROM stills WHERE path NOT LIKE '%/_factory/%'").fetchall()
    finally:
        con.close()
    for row in rows:
        stored = str(row["path"] or "")
        cid = extract_content_id(stored)
        if not cid:
            continue
        resolved = resolve_catalog_still_path(stored, input_root=root)
        if resolved is None or not resolved.is_file():
            continue
        yield cid, resolved


def backfill(
    *,
    data_root: Path,
    limit: Optional[int] = None,
    export: bool = False,
    skip_existing: bool = True,
    batch_size: Optional[int] = None,
) -> Dict[str, Any]:
    idx = SqliteEmbedIndex(default_db_path(data_root=data_root))
    ensure_db(idx.db_path)
    export_dir = default_export_dir(data_root=data_root) if export else None
    existing: set[str] = set()
    if skip_existing:
        con = connect(idx.db_path)
        try:
            existing = {str(r[0]) for r in con.execute("SELECT content_id FROM embeddings")}
        finally:
            con.close()
    done = 0
    skipped = 0
    errors = 0
    last_model = None
    consecutive_oom = 0
    abort = False
    batch_n = int(batch_size) if batch_size is not None else _clip_batch_size()
    batch_n = max(1, min(64, batch_n))
    decode_workers = max(1, int(os.environ.get("STILL_CLIP_DECODE_WORKERS", "16") or "16"))
    print(
        f"[still-embed] batch={batch_n} decode_workers={decode_workers} pipeline=1{_cuda_mem_mib()}",
        file=sys.stderr,
        flush=True,
    )
    t0 = time.time()
    pending: List[Tuple[str, Path]] = []
    prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="still-clip-pf")
    prefetch: Optional[Future] = None
    prefetch_items: List[Tuple[str, Path]] = []

    def _progress(cid: str) -> None:
        elapsed = time.time() - t0
        rate = (done / elapsed) if elapsed > 0 else 0.0
        print(
            f"[still-embed] encoded={done} skipped={skipped} errors={errors} "
            f"rate={rate:.2f}/s batch={batch_n} last={cid[:12]}{_cuda_mem_mib()}",
            file=sys.stderr,
            flush=True,
        )

    def _commit_row(cid: str, path: Path, vec: Optional[List[float]], model_id: str, err: Optional[str]) -> None:
        nonlocal done, errors, last_model, consecutive_oom, abort
        if abort:
            return
        if err or vec is None:
            errors += 1
            is_oom = "out of memory" in str(err or "").lower()
            is_setup = str(err or "").startswith("No module named") or "clip_vision model not found" in str(err or "")
            if is_oom or is_setup:
                consecutive_oom += 1
            else:
                consecutive_oom = 0
            if errors <= 8 or errors % 50 == 0:
                print(f"[still-embed] error {cid} {path}: {err}", file=sys.stderr, flush=True)
            if consecutive_oom >= 5 or (done == 0 and is_setup):
                abort = True
                print(
                    f"[still-embed] abort consecutive_failures={consecutive_oom} encoded={done} errors={errors} last={err}",
                    file=sys.stderr,
                    flush=True,
                )
            return
        idx.upsert(cid, vec, model_id=model_id, source_path=str(path))
        existing.add(cid)
        last_model = model_id
        consecutive_oom = 0
        if export_dir is not None:
            export_json(cid, {"model_id": model_id, "vector": vec, "source_path": str(path)}, export_dir=export_dir)
        done += 1
        if done == 1 or done % 32 == 0:
            _progress(cid)

    def _commit_encoded(
        rows: Sequence[Tuple[str, Path, Optional[List[float]], str, Optional[str]]],
    ) -> None:
        nonlocal done, last_model, consecutive_oom, abort
        ok: List[Tuple[str, Sequence[float], str, str]] = []
        last_cid = ""
        for cid, path, vec, model_id, err in rows:
            if abort:
                return
            if err or vec is None:
                _commit_row(cid, path, None, model_id or "", err or "encode_failed")
                continue
            if export_dir is not None:
                export_json(cid, {"model_id": model_id, "vector": vec, "source_path": str(path)}, export_dir=export_dir)
            ok.append((cid, vec, model_id, str(path)))
            last_cid = cid
        if not ok:
            return
        if limit is not None:
            remain = int(limit) - done
            if remain <= 0:
                abort = True
                return
            if len(ok) > remain:
                ok = ok[:remain]
                last_cid = str(ok[-1][0])
        written = idx.upsert_many(ok)
        for cid, _vec, model_id, _path in ok:
            existing.add(cid)
            last_model = model_id
        consecutive_oom = 0
        prev = done
        done += written
        if last_cid and (prev == 0 or done % 32 == 0 or prev // 32 != done // 32):
            _progress(last_cid)
        if limit is not None and done >= int(limit):
            abort = True

    def _empty_cuda() -> None:
        try:
            import torch  # type: ignore

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def _encode_direct(chunk: Sequence[Tuple[str, Path]]) -> None:
        if abort or not chunk:
            return
        try:
            rows = _encode_comfy_paths(list(chunk))
        except Exception as exc:
            msg = _short_exc(exc)
            if "out of memory" in msg.lower() and len(chunk) > 1:
                print(f"[still-embed] batch OOM size={len(chunk)}; retrying singles", file=sys.stderr, flush=True)
                _empty_cuda()
                for item in chunk:
                    if abort:
                        return
                    _encode_direct([item])
                return
            for cid, path in chunk:
                _commit_row(cid, path, None, "", msg)
            return
        _commit_encoded(rows)

    def _encode_ready(
        items: Sequence[Tuple[str, Path]],
        decoded: Sequence[Optional[Any]],
        dec_errors: Sequence[Optional[str]],
    ) -> None:
        if abort or not items:
            return
        _, model_id = _comfy_clip_vision()
        try:
            rows = _encode_decoded_batch(items, decoded, dec_errors, model_id=model_id)
        except Exception as exc:
            msg = _short_exc(exc)
            if "out of memory" in msg.lower() and len(items) > 1:
                print(f"[still-embed] batch OOM size={len(items)}; retrying singles", file=sys.stderr, flush=True)
                _empty_cuda()
                for item in items:
                    if abort:
                        return
                    _encode_direct([item])
                return
            for cid, path in items:
                _commit_row(cid, path, None, "", msg)
            return
        _commit_encoded(rows)

    def _submit_decode(chunk: Sequence[Tuple[str, Path]]) -> None:
        nonlocal prefetch, prefetch_items
        items = list(chunk)
        prefetch_items = items
        model, _model_id = _comfy_clip_vision()
        size = int(getattr(model, "image_size", 224) or 224)
        prefetch = prefetch_pool.submit(_decode_comfy_batch, items, size=size)

    def _flush(chunk: Sequence[Tuple[str, Path]]) -> None:
        nonlocal prefetch, prefetch_items, abort
        if abort or not chunk:
            return
        chunk = list(chunk)
        if prefetch is None:
            try:
                _submit_decode(chunk)
            except Exception as exc:
                msg = _short_exc(exc)
                for cid, path in chunk:
                    _commit_row(cid, path, None, "", msg)
                prefetch = None
                prefetch_items = []
            return
        decoded, dec_errors = prefetch.result()
        ready = prefetch_items
        try:
            _submit_decode(chunk)
        except Exception as exc:
            msg = _short_exc(exc)
            prefetch = None
            prefetch_items = []
            _encode_ready(ready, decoded, dec_errors)
            for cid, path in chunk:
                _commit_row(cid, path, None, "", msg)
            return
        _encode_ready(ready, decoded, dec_errors)

    try:
        for cid, path in _iter_catalog_stills(data_root=data_root):
            if abort:
                break
            if skip_existing and cid in existing:
                skipped += 1
                continue
            pending.append((cid, path))
            if len(pending) >= batch_n:
                _flush(pending)
                pending = []
            if abort:
                break
        if pending and not abort:
            _flush(pending)
        if prefetch is not None and not abort:
            decoded, dec_errors = prefetch.result()
            _encode_ready(prefetch_items, decoded, dec_errors)
    finally:
        prefetch_pool.shutdown(wait=False)
    stats = idx.stats()
    stats.update({"encoded": done, "skipped": skipped, "errors": errors, "last_model_id": last_model, "batch": batch_n})
    return stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Input-still CLIP embedding index")
    sub = p.add_subparsers(dest="cmd", required=True)
    bf = sub.add_parser("backfill", help="Encode catalog stills into sqlite")
    bf.add_argument("--data-root", type=Path, default=None)
    bf.add_argument("--limit", type=int, default=None)
    bf.add_argument("--export-json", action="store_true")
    bf.add_argument("--batch", type=int, default=None, help="CLIP batch size (default STILL_CLIP_BATCH or 16)")
    bf.add_argument("--reencode", action="store_true")
    st = sub.add_parser("stats", help="Print index counts")
    st.add_argument("--data-root", type=Path, default=None)
    args = p.parse_args(list(argv) if argv is not None else None)
    data_root = Path(args.data_root).expanduser().resolve() if args.data_root else Path(
        os.environ.get("SHAPE_FACTORY_DATA_ROOT") or (Path(__file__).resolve().parents[2] / ".data")
    )
    if args.cmd == "stats":
        print(json.dumps(SqliteEmbedIndex(default_db_path(data_root=data_root)).stats(), indent=2))
        return 0
    if args.cmd == "backfill":
        out = backfill(
            data_root=data_root,
            limit=args.limit,
            export=bool(args.export_json),
            skip_existing=not bool(args.reencode),
            batch_size=args.batch,
        )
        print(json.dumps(out, indent=2))
        return 0 if int(out.get("errors") or 0) == 0 or int(out.get("encoded") or 0) > 0 else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
