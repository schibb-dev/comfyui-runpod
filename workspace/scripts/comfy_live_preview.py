"""In-process ComfyUI /ws bridge: cache live latent preview frames by prompt_id."""

from __future__ import annotations

import json
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Comfy BinaryEventTypes (protocol.py)
BINARY_EVENT_PREVIEW_IMAGE = 1
BINARY_EVENT_PREVIEW_IMAGE_WITH_METADATA = 4
# Image formats in the 8-byte header (second u32) for PREVIEW_IMAGE
FORMAT_JPEG = 1
FORMAT_PNG = 2

# Advertise so Comfy 0.7+ sends PREVIEW_IMAGE_WITH_METADATA (and still gets frames).
CLIENT_FEATURE_FLAGS: Dict[str, bool] = {
    "supports_preview_metadata": True,
}

# Submitters historically used both hyphen and underscore forms; listen for both.
DEFAULT_CLIENT_IDS: Tuple[str, ...] = (
    "shape_factory",
    "shape-factory",
    "factory-map-ui",
    "experiments-ui",
)
DEFAULT_MAX_ENTRIES = 32
DEFAULT_FINISHED_TTL_S = 45.0
DEFAULT_STALE_TTL_S = 600.0
DEFAULT_MAX_VHS_FRAMES = 64


def ws_url_from_server(server: str, *, client_id: str) -> str:
    s = str(server or "").rstrip("/")
    if s.startswith("https://"):
        ws_base = "wss://" + s[len("https://") :]
    elif s.startswith("http://"):
        ws_base = "ws://" + s[len("http://") :]
    elif s.startswith("ws://") or s.startswith("wss://"):
        ws_base = s
    else:
        ws_base = "ws://" + s
    if ws_base.endswith("/ws"):
        return f"{ws_base}?clientId={client_id}"
    return f"{ws_base}/ws?clientId={client_id}"


def parse_preview_binary(
    payload: bytes,
) -> Optional[Tuple[bytes, str, Optional[int], Optional[str]]]:
    """
    Parse a Comfy binary preview frame.

    Wire layout (after Comfy ``encode_bytes``):
      u32 event_type | image_payload…

    PREVIEW_IMAGE (1): u32 image_format | image_bytes
    PREVIEW_IMAGE_WITH_METADATA (4): u32 meta_len | meta_json | image_bytes
    VHS animated preview embeds: u32 1 | u32 1 | u32 frame_index | 16p node | jpeg

    Returns (image_bytes, mime, frame_index_or_None, prompt_id_or_None) or None.
    """
    if not isinstance(payload, (bytes, bytearray)) or len(payload) < 9:
        return None
    buf = bytes(payload)
    try:
        event_type = struct.unpack_from(">I", buf, 0)[0]
    except struct.error:
        return None

    def _sniff(data: bytes) -> Optional[Tuple[int, str, bytes]]:
        jpeg_at = data.find(b"\xff\xd8")
        png_at = data.find(b"\x89PNG\r\n\x1a\n")
        candidates: List[Tuple[int, str, bytes]] = []
        if jpeg_at >= 0:
            candidates.append((jpeg_at, "image/jpeg", data[jpeg_at:]))
        if png_at >= 0:
            candidates.append((png_at, "image/png", data[png_at:]))
        if not candidates:
            return None
        candidates.sort(key=lambda t: t[0])
        return candidates[0]

    # Comfy 0.7+ metadata-bearing previews.
    if event_type == BINARY_EVENT_PREVIEW_IMAGE_WITH_METADATA:
        if len(buf) < 8:
            return None
        try:
            meta_len = struct.unpack_from(">I", buf, 4)[0]
        except struct.error:
            return None
        meta_start = 8
        meta_end = meta_start + int(meta_len)
        if meta_len < 0 or meta_end > len(buf):
            return None
        prompt_id: Optional[str] = None
        frame_idx: Optional[int] = None
        try:
            meta_obj = json.loads(buf[meta_start:meta_end].decode("utf-8"))
            if isinstance(meta_obj, dict):
                pid = meta_obj.get("prompt_id")
                if isinstance(pid, str) and pid.strip():
                    prompt_id = pid.strip()
                for key in ("frame_index", "index", "frame"):
                    if key in meta_obj:
                        try:
                            frame_idx = int(meta_obj[key])
                        except (TypeError, ValueError):
                            pass
                        break
                mime_hint = str(meta_obj.get("image_type") or "").strip().lower()
            else:
                mime_hint = ""
        except Exception:
            mime_hint = ""
        image = buf[meta_end:]
        if not image:
            return None
        if image[:2] == b"\xff\xd8" or mime_hint == "image/jpeg":
            return image, "image/jpeg", frame_idx, prompt_id
        if image[:8] == b"\x89PNG\r\n\x1a\n" or mime_hint == "image/png":
            return image, "image/png", frame_idx, prompt_id
        sniffed = _sniff(image)
        if sniffed is None:
            return None
        return sniffed[2], sniffed[1], frame_idx, prompt_id

    try:
        _event_type, image_format = struct.unpack_from(">II", buf, 0)
    except struct.error:
        return None
    event_type = _event_type
    # Some builds send little-endian; accept either when event looks wrong.
    if event_type != BINARY_EVENT_PREVIEW_IMAGE:
        try:
            event_type_le, image_format_le = struct.unpack_from("<II", buf, 0)
        except struct.error:
            return None
        if event_type_le == BINARY_EVENT_PREVIEW_IMAGE:
            event_type, image_format = event_type_le, image_format_le
        else:
            image_format = 0

    frame_idx = None
    sniffed = _sniff(buf[4:])
    if sniffed is not None:
        off_in_tail, mime, blob = sniffed
        abs_off = 4 + off_in_tail
        # VHS layout after outer event: >I 1, >I 1, >I ind, 16p, image → image @ ≥32
        if abs_off >= 32 and len(buf) >= 16:
            try:
                a, b, ind = struct.unpack_from(">III", buf, 4)
                if a == 1 and b == 1 and 0 <= int(ind) < 10_000:
                    frame_idx = int(ind)
            except struct.error:
                pass
        return blob, mime, frame_idx, None
    data = buf[8:]
    if not data:
        return None
    if image_format == FORMAT_JPEG or data[:2] == b"\xff\xd8":
        return data, "image/jpeg", None, None
    if image_format == FORMAT_PNG or data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png", None, None
    if image_format in (FORMAT_JPEG, FORMAT_PNG, 0):
        return data, "image/jpeg" if image_format != FORMAT_PNG else "image/png", None, None
    return None


@dataclass
class LivePreviewEntry:
    prompt_id: str
    image: Optional[bytes] = None
    mime: str = "image/jpeg"
    value: Optional[int] = None
    max: Optional[int] = None
    node: Optional[str] = None
    status: str = "unknown"  # running | done | error | interrupted | unknown
    updated_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    started_at: Optional[float] = None
    frames: Dict[int, bytes] = field(default_factory=dict)
    frame_mimes: Dict[int, str] = field(default_factory=dict)
    vhs_length: Optional[int] = None
    vhs_rate: Optional[float] = None

    def to_status(self) -> Dict[str, Any]:
        now = time.time()
        started = self.started_at
        elapsed_s: Optional[float] = None
        eta_s: Optional[float] = None
        if started is not None and self.status == "running":
            elapsed_s = max(0.0, now - started)
            if (
                isinstance(self.value, int)
                and isinstance(self.max, int)
                and self.value > 0
                and self.max > self.value
            ):
                eta_s = elapsed_s * float(self.max - self.value) / float(self.value)
            elif (
                isinstance(self.value, int)
                and isinstance(self.max, int)
                and self.max > 0
                and self.value >= self.max
            ):
                eta_s = 0.0
        elif started is not None and self.finished_at is not None:
            elapsed_s = max(0.0, self.finished_at - started)
            eta_s = 0.0 if self.status == "done" else None
        frames_ready = sorted(self.frames.keys())
        return {
            "prompt_id": self.prompt_id,
            "has_preview": bool(self.image) or bool(self.frames),
            "value": self.value,
            "max": self.max,
            "node": self.node,
            "status": self.status,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "started_at": self.started_at,
            "elapsed_s": elapsed_s,
            "eta_s": eta_s,
            "mime": self.mime if self.image else None,
            "vhs_length": self.vhs_length,
            "vhs_rate": self.vhs_rate,
            "frames_ready": frames_ready,
            "frames_count": len(frames_ready),
        }


class LivePreviewCache:
    """Thread-safe in-memory cache of latest preview + progress per prompt_id."""

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        finished_ttl_s: float = DEFAULT_FINISHED_TTL_S,
        stale_ttl_s: float = DEFAULT_STALE_TTL_S,
        max_vhs_frames: int = DEFAULT_MAX_VHS_FRAMES,
    ) -> None:
        self._lock = threading.RLock()
        self._by_pid: Dict[str, LivePreviewEntry] = {}
        self.max_entries = max(1, int(max_entries))
        self.finished_ttl_s = float(finished_ttl_s)
        self.stale_ttl_s = float(stale_ttl_s)
        self.max_vhs_frames = max(1, int(max_vhs_frames))

    def _touch(self, prompt_id: str) -> LivePreviewEntry:
        pid = str(prompt_id or "").strip()
        ent = self._by_pid.get(pid)
        if ent is None:
            ent = LivePreviewEntry(prompt_id=pid)
            self._by_pid[pid] = ent
        ent.updated_at = time.time()
        return ent

    def _evict(self) -> None:
        now = time.time()
        drop: List[str] = []
        for pid, ent in self._by_pid.items():
            if ent.finished_at is not None and (now - ent.finished_at) > self.finished_ttl_s:
                drop.append(pid)
            elif (now - ent.updated_at) > self.stale_ttl_s:
                drop.append(pid)
        for pid in drop:
            self._by_pid.pop(pid, None)
        if len(self._by_pid) <= self.max_entries:
            return
        while len(self._by_pid) > self.max_entries:
            ordered = sorted(
                self._by_pid.items(),
                key=lambda kv: (
                    0 if kv[1].finished_at is not None else 1,
                    kv[1].finished_at or 0.0,
                    kv[1].updated_at,
                ),
            )
            if not ordered:
                break
            self._by_pid.pop(ordered[0][0], None)

    def on_text_event(
        self,
        msg_type: str,
        data: Dict[str, Any],
        *,
        current_pid: Optional[str] = None,
    ) -> Optional[str]:
        """Apply a Comfy JSON WS event. Returns current prompt_id when known."""
        if not isinstance(data, dict):
            return None
        # VHS emits length/rate without prompt_id — attach to current execution.
        if msg_type == "VHS_latentpreview":
            pid = str(current_pid or "").strip()
            if not pid:
                return None
            with self._lock:
                ent = self._touch(pid)
                try:
                    length = int(data.get("length"))
                except (TypeError, ValueError):
                    length = None
                try:
                    rate = float(data.get("rate"))
                except (TypeError, ValueError):
                    rate = None
                if length is not None and length > 0:
                    ent.vhs_length = min(length, self.max_vhs_frames * 2)
                if rate is not None and rate > 0:
                    ent.vhs_rate = rate
                if ent.started_at is None:
                    ent.started_at = time.time()
                ent.status = "running"
                ent.finished_at = None
                self._evict()
            return pid

        pid = data.get("prompt_id")
        if not isinstance(pid, str) or not pid.strip():
            return None
        pid = pid.strip()
        with self._lock:
            ent = self._touch(pid)
            if msg_type == "execution_start":
                ent.status = "running"
                ent.finished_at = None
                ent.started_at = time.time()
                ent.frames.clear()
                ent.frame_mimes.clear()
                ent.vhs_length = None
                ent.vhs_rate = None
                ent.image = None
                ent.value = None
                ent.max = None
            elif msg_type == "executing":
                node = data.get("node")
                if node is None:
                    if ent.status not in ("error", "interrupted"):
                        ent.status = "done"
                    ent.finished_at = time.time()
                    if ent.started_at is None:
                        ent.started_at = ent.finished_at
                else:
                    ent.status = "running"
                    ent.node = str(node)
                    ent.finished_at = None
                    if ent.started_at is None:
                        ent.started_at = time.time()
            elif msg_type == "progress":
                try:
                    ent.value = int(data.get("value"))
                except (TypeError, ValueError):
                    pass
                try:
                    ent.max = int(data.get("max"))
                except (TypeError, ValueError):
                    pass
                ent.status = "running"
                ent.finished_at = None
                if ent.started_at is None:
                    ent.started_at = time.time()
            elif msg_type == "execution_success":
                ent.status = "done"
                ent.finished_at = time.time()
                if ent.started_at is None:
                    ent.started_at = ent.finished_at
            elif msg_type == "execution_error":
                ent.status = "error"
                ent.finished_at = time.time()
                if ent.started_at is None:
                    ent.started_at = ent.finished_at
            elif msg_type == "execution_interrupted":
                ent.status = "interrupted"
                ent.finished_at = time.time()
                if ent.started_at is None:
                    ent.started_at = ent.finished_at
            self._evict()
            return pid

    def on_preview_bytes(
        self,
        prompt_id: str,
        image: bytes,
        mime: str,
        *,
        frame_index: Optional[int] = None,
    ) -> None:
        pid = str(prompt_id or "").strip()
        if not pid or not image:
            return
        with self._lock:
            ent = self._touch(pid)
            ent.image = image
            ent.mime = mime or "image/jpeg"
            if frame_index is not None and frame_index >= 0:
                idx = int(frame_index)
                ent.frames[idx] = image
                ent.frame_mimes[idx] = mime or "image/jpeg"
                if len(ent.frames) > self.max_vhs_frames:
                    for old in sorted(ent.frames.keys())[: len(ent.frames) - self.max_vhs_frames]:
                        ent.frames.pop(old, None)
                        ent.frame_mimes.pop(old, None)
                if ent.vhs_length is None or ent.vhs_length < idx + 1:
                    ent.vhs_length = max(idx + 1, ent.vhs_length or 0)
            if ent.status in ("unknown", "done"):
                ent.status = "running"
                ent.finished_at = None
            if ent.started_at is None:
                ent.started_at = time.time()
            self._evict()

    def get_entry(self, prompt_id: str) -> Optional[LivePreviewEntry]:
        pid = str(prompt_id or "").strip()
        if not pid:
            return None
        with self._lock:
            self._evict()
            ent = self._by_pid.get(pid)
            if not ent:
                return None
            return LivePreviewEntry(
                prompt_id=ent.prompt_id,
                image=ent.image,
                mime=ent.mime,
                value=ent.value,
                max=ent.max,
                node=ent.node,
                status=ent.status,
                updated_at=ent.updated_at,
                finished_at=ent.finished_at,
                started_at=ent.started_at,
                frames=dict(ent.frames),
                frame_mimes=dict(ent.frame_mimes),
                vhs_length=ent.vhs_length,
                vhs_rate=ent.vhs_rate,
            )

    def get_image(self, prompt_id: str, *, frame: Optional[int] = None) -> Optional[Tuple[bytes, str]]:
        ent = self.get_entry(prompt_id)
        if ent is None:
            return None
        if frame is not None:
            img = ent.frames.get(int(frame))
            if img:
                return img, ent.frame_mimes.get(int(frame), ent.mime or "image/jpeg")
            return None
        if ent.image:
            return ent.image, ent.mime
        if ent.frames:
            last = max(ent.frames.keys())
            return ent.frames[last], ent.frame_mimes.get(last, "image/jpeg")
        return None

    def status_items(self, prompt_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        with self._lock:
            self._evict()
            empty = {
                "has_preview": False,
                "value": None,
                "max": None,
                "node": None,
                "status": "unknown",
                "updated_at": None,
                "finished_at": None,
                "started_at": None,
                "elapsed_s": None,
                "eta_s": None,
                "mime": None,
                "vhs_length": None,
                "vhs_rate": None,
                "frames_ready": [],
                "frames_count": 0,
            }
            if prompt_ids:
                ids = [str(p or "").strip() for p in prompt_ids if str(p or "").strip()]
                out = []
                for pid in ids:
                    ent = self._by_pid.get(pid)
                    if ent:
                        out.append(ent.to_status())
                    else:
                        out.append({"prompt_id": pid, **empty})
                return out
            return [e.to_status() for e in self._by_pid.values()]


class LivePreviewBridge:
    """Background WebSocket subscriber(s) feeding a shared LivePreviewCache."""

    def __init__(
        self,
        *,
        comfy_server: str,
        client_ids: Sequence[str] = DEFAULT_CLIENT_IDS,
        cache: Optional[LivePreviewCache] = None,
    ) -> None:
        self.comfy_server = str(comfy_server or "").rstrip("/") or "http://127.0.0.1:8188"
        self.client_ids = tuple(dict.fromkeys(str(c).strip() for c in client_ids if str(c).strip())) or DEFAULT_CLIENT_IDS
        self.cache = cache or LivePreviewCache()
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        for cid in self.client_ids:
            t = threading.Thread(
                target=self._run_client_loop,
                args=(cid,),
                name=f"comfy-live-preview:{cid}",
                daemon=True,
            )
            self._threads.append(t)
            t.start()

    def stop(self) -> None:
        self._stop.set()

    def _run_client_loop(self, client_id: str) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._run_one_socket(client_id)
                backoff = 1.0
            except Exception as e:
                print(f"[comfy-live-preview] client={client_id} disconnected: {e}")
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(30.0, backoff * 1.7)

    def _run_one_socket(self, client_id: str) -> None:
        ws_url = ws_url_from_server(self.comfy_server, client_id=client_id)
        try:
            self._run_aiohttp(ws_url, client_id)
            return
        except ImportError:
            pass
        self._run_websocket_client(ws_url, client_id)

    def _handle_text(self, raw: str, current_pid: Optional[str]) -> Optional[str]:
        try:
            obj = json.loads(raw)
        except Exception:
            return current_pid
        if not isinstance(obj, dict):
            return current_pid
        msg_type = obj.get("type")
        data = obj.get("data")
        if not isinstance(msg_type, str) or not isinstance(data, dict):
            return current_pid
        pid = self.cache.on_text_event(msg_type, data, current_pid=current_pid)
        return pid or current_pid

    def _handle_binary(self, payload: bytes, current_pid: Optional[str]) -> None:
        parsed = parse_preview_binary(payload)
        if not parsed:
            return
        image, mime, frame_idx, meta_pid = parsed
        pid = (meta_pid or current_pid or "").strip()
        if not pid:
            return
        self.cache.on_preview_bytes(pid, image, mime, frame_index=frame_idx)

    def _run_aiohttp(self, ws_url: str, client_id: str) -> None:
        import asyncio

        import aiohttp  # type: ignore

        async def _go() -> None:
            timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(ws_url, heartbeat=20, max_msg_size=16 * 1024 * 1024) as ws:
                    # Comfy 0.7+ feature negotiation (first message) enables metadata previews.
                    try:
                        await ws.send_json({"type": "feature_flags", "data": CLIENT_FEATURE_FLAGS})
                    except Exception:
                        pass
                    print(f"[comfy-live-preview] connected client={client_id} url={ws_url}")
                    current_pid: Optional[str] = None
                    async for msg in ws:
                        if self._stop.is_set():
                            await ws.close()
                            return
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            current_pid = self._handle_text(str(msg.data), current_pid)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            data = msg.data
                            if isinstance(data, memoryview):
                                data = data.tobytes()
                            elif isinstance(data, bytearray):
                                data = bytes(data)
                            if isinstance(data, bytes):
                                self._handle_binary(data, current_pid)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            raise RuntimeError(f"ws closed/error client={client_id}")

        asyncio.run(_go())

    def _run_websocket_client(self, ws_url: str, client_id: str) -> None:
        import websocket  # type: ignore

        current_pid: Dict[str, Optional[str]] = {"pid": None}

        def on_open(ws: Any) -> None:
            try:
                ws.send(json.dumps({"type": "feature_flags", "data": CLIENT_FEATURE_FLAGS}))
            except Exception:
                pass

        def on_message(_ws: Any, message: Any) -> None:
            if isinstance(message, bytes):
                self._handle_binary(message, current_pid["pid"])
            elif isinstance(message, str):
                current_pid["pid"] = self._handle_text(message, current_pid["pid"])

        def on_error(_ws: Any, error: Any) -> None:
            raise RuntimeError(str(error))

        def on_close(_ws: Any, *_args: Any) -> None:
            raise RuntimeError("ws closed")

        print(f"[comfy-live-preview] connecting client={client_id} url={ws_url}")
        app = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        app.run_forever(ping_interval=20, ping_timeout=10)


_BRIDGE: Optional[LivePreviewBridge] = None
_BRIDGE_LOCK = threading.Lock()


def get_bridge() -> Optional[LivePreviewBridge]:
    return _BRIDGE


def start_bridge(comfy_server: str, *, client_ids: Optional[Iterable[str]] = None) -> LivePreviewBridge:
    global _BRIDGE
    with _BRIDGE_LOCK:
        if _BRIDGE is not None:
            return _BRIDGE
        ids = tuple(client_ids) if client_ids is not None else DEFAULT_CLIENT_IDS
        bridge = LivePreviewBridge(comfy_server=comfy_server, client_ids=ids)
        bridge.start()
        _BRIDGE = bridge
        print(f"[comfy-live-preview] bridge started server={comfy_server} clients={list(ids)}")
        return bridge


def live_status_payload(prompt_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    bridge = get_bridge()
    if bridge is None:
        return {"ok": True, "bridge": False, "items": [], "count": 0}
    items = bridge.cache.status_items(prompt_ids)
    return {"ok": True, "bridge": True, "items": items, "count": len(items)}


def live_preview_image(prompt_id: str, *, frame: Optional[int] = None) -> Optional[Tuple[bytes, str]]:
    bridge = get_bridge()
    if bridge is None:
        return None
    return bridge.cache.get_image(prompt_id, frame=frame)
