#!/usr/bin/env python3
"""Tests for comfy_live_preview cache + binary frame parsing."""

from __future__ import annotations

import struct
import time
import unittest

import support  # noqa: F401
from comfy_live_preview import (
    BINARY_EVENT_PREVIEW_IMAGE,
    FORMAT_JPEG,
    FORMAT_PNG,
    LivePreviewCache,
    parse_preview_binary,
    ws_url_from_server,
)


class ParseBinaryTests(unittest.TestCase):
    def test_parse_jpeg_be(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"\x00" * 20
        payload = struct.pack(">II", BINARY_EVENT_PREVIEW_IMAGE, FORMAT_JPEG) + jpeg
        out = parse_preview_binary(payload)
        self.assertIsNotNone(out)
        assert out is not None
        data, mime, frame = out
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertIsNone(frame)

    def test_parse_png_be(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 12
        payload = struct.pack(">II", BINARY_EVENT_PREVIEW_IMAGE, FORMAT_PNG) + png
        out = parse_preview_binary(payload)
        self.assertIsNotNone(out)
        assert out is not None
        data, mime, frame = out
        self.assertEqual(mime, "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertIsNone(frame)

    def test_parse_vhs_extra_headers(self) -> None:
        """VHS wraps JPEG with index + 16-byte node id inside the PREVIEW_IMAGE payload."""
        jpeg = b"\xff\xd8\xff" + b"\x00" * 40
        vhs_body = (
            struct.pack(">II", 1, 1)  # duplicated event/format embedded by VHS
            + struct.pack(">I", 3)  # frame index
            + struct.pack("16p", b"136")  # node id
            + jpeg
        )
        payload = struct.pack(">I", BINARY_EVENT_PREVIEW_IMAGE) + vhs_body
        out = parse_preview_binary(payload)
        self.assertIsNotNone(out)
        assert out is not None
        data, mime, frame = out
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertEqual(data, jpeg)
        self.assertEqual(frame, 3)

    def test_parse_rejects_short(self) -> None:
        self.assertIsNone(parse_preview_binary(b"short"))

    def test_ws_url(self) -> None:
        self.assertEqual(
            ws_url_from_server("http://127.0.0.1:8188", client_id="shape-factory"),
            "ws://127.0.0.1:8188/ws?clientId=shape-factory",
        )


class CacheTests(unittest.TestCase):
    def test_progress_and_preview(self) -> None:
        cache = LivePreviewCache(max_entries=8, finished_ttl_s=0.2)
        pid = cache.on_text_event(
            "progress",
            {"prompt_id": "abc", "value": 3, "max": 20},
        )
        self.assertEqual(pid, "abc")
        jpeg = b"\xff\xd8\xff" + b"\x11" * 16
        cache.on_preview_bytes("abc", jpeg, "image/jpeg")
        got = cache.get_image("abc")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got[0], jpeg)
        status = cache.status_items(["abc"])[0]
        self.assertTrue(status["has_preview"])
        self.assertEqual(status["value"], 3)
        self.assertEqual(status["max"], 20)
        self.assertIsNotNone(status["elapsed_s"])
        self.assertIsNotNone(status["eta_s"])
        self.assertGreater(status["eta_s"], 0)

    def test_vhs_frames_and_event(self) -> None:
        cache = LivePreviewCache(max_entries=8)
        cache.on_text_event("execution_start", {"prompt_id": "v1"})
        cache.on_text_event(
            "VHS_latentpreview",
            {"length": 8, "rate": 4.0, "id": "150"},
            current_pid="v1",
        )
        for i in range(4):
            cache.on_preview_bytes("v1", b"\xff\xd8" + bytes([i]) * 8, "image/jpeg", frame_index=i)
        st = cache.status_items(["v1"])[0]
        self.assertEqual(st["vhs_length"], 8)
        self.assertEqual(st["vhs_rate"], 4.0)
        self.assertEqual(st["frames_count"], 4)
        self.assertEqual(cache.get_image("v1", frame=2)[0][:2], b"\xff\xd8")

    def test_finished_ttl_eviction(self) -> None:
        cache = LivePreviewCache(max_entries=8, finished_ttl_s=0.05)
        cache.on_text_event("execution_start", {"prompt_id": "done1"})
        cache.on_preview_bytes("done1", b"\xff\xd8\xffxx", "image/jpeg")
        cache.on_text_event("execution_success", {"prompt_id": "done1"})
        time.sleep(0.08)
        self.assertIsNone(cache.get_image("done1"))

    def test_max_entries_evicts_oldest(self) -> None:
        cache = LivePreviewCache(max_entries=3, stale_ttl_s=3600)
        for i in range(5):
            pid = f"p{i}"
            cache.on_text_event("progress", {"prompt_id": pid, "value": 1, "max": 2})
            cache.on_preview_bytes(pid, b"\xff\xd8" + bytes([i]) * 8, "image/jpeg")
        self.assertLessEqual(len(cache.status_items()), 3)


if __name__ == "__main__":
    unittest.main()
