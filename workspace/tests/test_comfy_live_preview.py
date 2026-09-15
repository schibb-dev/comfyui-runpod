#!/usr/bin/env python3
"""Tests for comfy_live_preview cache + binary frame parsing."""

from __future__ import annotations

import json
import struct
import tempfile
import time
import unittest

import support  # noqa: F401
from comfy_live_preview import (
    BINARY_EVENT_PREVIEW_IMAGE,
    BINARY_EVENT_PREVIEW_IMAGE_WITH_METADATA,
    DEFAULT_CLIENT_IDS,
    FORMAT_JPEG,
    FORMAT_PNG,
    LivePreviewCache,
    client_ids_from_queue_payload,
    parse_preview_binary,
    queue_prompt_ids_from_payload,
    should_listen_for_client_id,
    ws_url_from_server,
)


class ParseBinaryTests(unittest.TestCase):
    def test_parse_jpeg_be(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"\x00" * 20
        payload = struct.pack(">II", BINARY_EVENT_PREVIEW_IMAGE, FORMAT_JPEG) + jpeg
        out = parse_preview_binary(payload)
        self.assertIsNotNone(out)
        assert out is not None
        data, mime, frame, _pid = out
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertIsNone(frame)

    def test_parse_png_be(self) -> None:
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 12
        payload = struct.pack(">II", BINARY_EVENT_PREVIEW_IMAGE, FORMAT_PNG) + png
        out = parse_preview_binary(payload)
        self.assertIsNotNone(out)
        assert out is not None
        data, mime, frame, _pid = out
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
        data, mime, frame, _pid = out
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(data[:2], b"\xff\xd8")
        self.assertEqual(data, jpeg)
        self.assertEqual(frame, 3)

    def test_parse_preview_with_metadata(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"\x00" * 24
        meta = json.dumps({"prompt_id": "pid-meta", "node_id": "136", "image_type": "image/jpeg"}).encode()
        payload = (
            struct.pack(">I", BINARY_EVENT_PREVIEW_IMAGE_WITH_METADATA)
            + struct.pack(">I", len(meta))
            + meta
            + jpeg
        )
        out = parse_preview_binary(payload)
        self.assertIsNotNone(out)
        assert out is not None
        data, mime, frame, pid = out
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(data, jpeg)
        self.assertIsNone(frame)
        self.assertEqual(pid, "pid-meta")

    def test_parse_rejects_short(self) -> None:
        self.assertIsNone(parse_preview_binary(b"short"))

    def test_ws_url(self) -> None:
        self.assertEqual(
            ws_url_from_server("http://127.0.0.1:8188", client_id="shape-factory"),
            "ws://127.0.0.1:8188/ws?clientId=shape-factory",
        )


class ClientIdRoutingTests(unittest.TestCase):
    def test_default_client_ids_include_http_submitters(self) -> None:
        for cid in ("comfy_tool", "factory-map-ui", "shape_factory"):
            self.assertIn(cid, DEFAULT_CLIENT_IDS)

    def test_queue_payload_extracts_running_and_pending(self) -> None:
        payload = {
            "queue_running": [[3, "pid-run", {}, {"client_id": "comfy_tool"}]],
            "queue_pending": [
                [1, "pid-a", {}, {"client_id": "factory-map-ui"}],
                [2, "pid-b", {}, {"client_id": "compare-480p-q8-432x768"}],
                [3, "pid-dup", {}, {"client_id": "comfy_tool"}],
            ],
        }
        self.assertEqual(
            client_ids_from_queue_payload(payload),
            ["comfy_tool", "factory-map-ui", "compare-480p-q8-432x768"],
        )
        self.assertEqual(
            queue_prompt_ids_from_payload(payload),
            ["pid-run", "pid-a", "pid-b", "pid-dup"],
        )

    def test_listen_skips_comfy_frontend_uuids(self) -> None:
        self.assertTrue(should_listen_for_client_id("comfy_tool"))
        self.assertTrue(should_listen_for_client_id("compare-480p-q8-432x768"))
        self.assertFalse(should_listen_for_client_id("7192f4bb-475c-43f2-bde7-5ffdfb370fef"))
        self.assertFalse(should_listen_for_client_id("a" * 32))
        self.assertFalse(should_listen_for_client_id(""))


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

    def test_orphan_flush_on_execution_start(self) -> None:
        cache = LivePreviewCache(max_entries=8, orphan_ttl_s=5.0)
        jpeg = b"\xff\xd8\xff" + b"\x22" * 12
        cache.stash_orphan_preview(jpeg, "image/jpeg", frame_index=0)
        self.assertIsNone(cache.get_image("later"))
        cache.on_text_event("execution_start", {"prompt_id": "later"})
        got = cache.get_image("later")
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got[0], jpeg)
        st = cache.status_items(["later"])[0]
        self.assertTrue(st["has_preview"])
        self.assertEqual(st["frames_count"], 1)

    def test_progress_state_maps_running_node(self) -> None:
        cache = LivePreviewCache(max_entries=8)
        pid = cache.on_text_event(
            "progress_state",
            {
                "prompt_id": "ps-1",
                "nodes": {
                    "10": {"value": 2, "max": 14, "state": "running", "node_id": "10"},
                    "9": {"value": 1, "max": 1, "state": "finished", "node_id": "9"},
                },
            },
        )
        self.assertEqual(pid, "ps-1")
        st = cache.status_items(["ps-1"])[0]
        self.assertEqual(st["value"], 2)
        self.assertEqual(st["max"], 14)
        self.assertEqual(st["node"], "10")
        self.assertEqual(st["status"], "running")

    def test_progress_falls_back_to_current_pid(self) -> None:
        cache = LivePreviewCache(max_entries=8)
        cache.note_queue_running("run-q")
        pid = cache.on_text_event("progress", {"value": 3, "max": 14}, current_pid="run-q")
        self.assertEqual(pid, "run-q")
        st = cache.status_items(["run-q"])[0]
        self.assertFalse(st["has_preview"])
        self.assertEqual(st["value"], 3)
        self.assertEqual(st["max"], 14)
        self.assertEqual(st["status"], "running")

    def test_guess_preview_pid_uses_running(self) -> None:
        cache = LivePreviewCache(max_entries=8)
        cache.on_text_event("execution_start", {"prompt_id": "run-a"})
        cache.on_text_event("progress", {"prompt_id": "run-a", "value": 1, "max": 10})
        self.assertEqual(cache.guess_preview_pid(), "run-a")
        jpeg = b"\xff\xd8\xffyy"
        # Simulate binary without metadata attaching via guess
        pid = cache.guess_preview_pid()
        assert pid is not None
        cache.on_preview_bytes(pid, jpeg, "image/jpeg")
        self.assertEqual(cache.get_image("run-a")[0], jpeg)


class PersistTests(unittest.TestCase):
    def test_late_client_rehydrates_latest_still(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"\x33" * 12
        with tempfile.TemporaryDirectory() as tmp:
            a = LivePreviewCache(persist_dir=tmp)
            a.on_preview_bytes("pid-keep", jpeg, "image/jpeg")
            b = LivePreviewCache(persist_dir=tmp)
            got = b.get_image("pid-keep")
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got[0], jpeg)
            self.assertTrue(b.status_items(["pid-keep"])[0]["has_preview"])

    def test_finish_invalidates_disk(self) -> None:
        jpeg = b"\xff\xd8\xff" + b"\x44" * 12
        with tempfile.TemporaryDirectory() as tmp:
            a = LivePreviewCache(persist_dir=tmp)
            a.on_preview_bytes("pid-done", jpeg, "image/jpeg")
            a.on_text_event("execution_success", {"prompt_id": "pid-done"})
            b = LivePreviewCache(persist_dir=tmp)
            self.assertIsNone(b.get_image("pid-done"))

    def test_retain_drops_ids_not_in_queue(self) -> None:
        jpeg = b"\xff\xd8\xffxx"
        with tempfile.TemporaryDirectory() as tmp:
            a = LivePreviewCache(persist_dir=tmp)
            a.on_preview_bytes("old-pid", jpeg, "image/jpeg")
            a.on_preview_bytes("live-pid", jpeg, "image/jpeg")
            a.retain_persisted(["live-pid"])
            b = LivePreviewCache(persist_dir=tmp)
            self.assertIsNone(b.get_image("old-pid"))
            got = b.get_image("live-pid")
            assert got is not None
            self.assertEqual(got[0], jpeg)


if __name__ == "__main__":
    unittest.main()
