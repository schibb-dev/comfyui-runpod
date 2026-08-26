#!/usr/bin/env python3
"""Shared stdlib HTTP helpers with transient retry/backoff semantics."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Sequence

_RETRY_HTTP_STATUSES = (429, 500, 502, 503, 504)
_RETRY_OS_ERRNOS = (104, 110, 111)  # reset, timed out, refused


def is_transient_http_error(exc: Exception) -> bool:
    """Best-effort classification for retryable network failures."""
    if isinstance(exc, (ConnectionResetError, ConnectionRefusedError, TimeoutError)):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, (ConnectionResetError, ConnectionRefusedError, TimeoutError)):
            return True
        if isinstance(reason, OSError):
            return int(getattr(reason, "errno", 0) or 0) in _RETRY_OS_ERRNOS
    return False


def _default_attempts(method: str, retry_attempts: Optional[int]) -> int:
    if retry_attempts is not None:
        return max(1, int(retry_attempts))
    m = str(method or "GET").upper().strip()
    return 3 if m in {"GET", "HEAD"} else 1


def urlopen_read_with_retry(
    *,
    method: str,
    url: str,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout_s: float = 10,
    retry_attempts: Optional[int] = None,
    retry_backoff_s: float = 0.25,
    retry_statuses: Sequence[int] = _RETRY_HTTP_STATUSES,
) -> bytes:
    """Request URL and return raw bytes, retrying transient failures."""
    meth = str(method or "GET").upper().strip()
    attempts = _default_attempts(meth, retry_attempts)
    last_exc: Optional[Exception] = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=data, headers=headers or {}, method=meth)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            if code in retry_statuses and attempt + 1 < attempts:
                time.sleep(float(retry_backoff_s) * (2**attempt))
                last_exc = exc
                continue
            raise
        except Exception as exc:
            if is_transient_http_error(exc) and attempt + 1 < attempts:
                time.sleep(float(retry_backoff_s) * (2**attempt))
                last_exc = exc
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("urlopen_read_with_retry_unreachable")


def http_json_with_retry(
    *,
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout_s: float = 10,
    retry_attempts: Optional[int] = None,
    retry_backoff_s: float = 0.25,
    accept: str = "application/json",
) -> Any:
    """JSON request helper with retry semantics."""
    headers: Dict[str, str] = {"Accept": accept}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    raw = urlopen_read_with_retry(
        method=method,
        url=url,
        data=data,
        headers=headers,
        timeout_s=timeout_s,
        retry_attempts=retry_attempts,
        retry_backoff_s=retry_backoff_s,
    )
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return {}
    return json.loads(text)


def http_text_with_retry(
    *,
    url: str,
    timeout_s: float = 10,
    retry_attempts: int = 3,
    retry_backoff_s: float = 0.25,
    accept: str = "text/plain, */*",
) -> str:
    """GET text helper with retry semantics."""
    raw = urlopen_read_with_retry(
        method="GET",
        url=url,
        headers={"Accept": accept},
        timeout_s=timeout_s,
        retry_attempts=retry_attempts,
        retry_backoff_s=retry_backoff_s,
    )
    return raw.decode("utf-8", "replace")
