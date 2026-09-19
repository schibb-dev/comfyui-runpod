#!/usr/bin/env python3
"""Stdio MCP server for factory verbs (hourly explore first).

No secrets. Writes go through the same schedule / ratings stores as Home.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from factory_mcp_ops import TOOL_SPECS, dispatch_tool  # noqa: E402

PROTOCOL = "2024-11-05"


def _read_message() -> Optional[Dict[str, Any]]:
    header_len = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        if line.lower().startswith(b"content-length:"):
            try:
                header_len = int(line.split(b":", 1)[1].strip())
            except ValueError:
                header_len = None
    if header_len is None:
        return None
    raw = sys.stdin.buffer.read(header_len)
    if not raw:
        return None
    try:
        msg = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    return msg if isinstance(msg, dict) else None


def _write_message(payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _result(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = str(msg.get("method") or "")
    req_id = msg.get("id")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        return _result(
            req_id,
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "factory", "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {"tools": TOOL_SPECS})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            payload = dispatch_tool(name, arguments)
        except Exception as exc:
            payload = {"ok": False, "error": "tool_failed", "detail": str(exc)}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return _result(
            req_id,
            {
                "content": [{"type": "text", "text": text}],
                "isError": not bool(payload.get("ok", True)),
            },
        )
    if req_id is None:
        return None
    return _error(req_id, -32601, f"unknown method {method}")


def main() -> int:
    while True:
        msg = _read_message()
        if msg is None:
            return 0
        reply = handle(msg)
        if reply is not None:
            _write_message(reply)


if __name__ == "__main__":
    raise SystemExit(main())
