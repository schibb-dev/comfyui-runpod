"""Split ledger persistence: compact hot meta JSON + SQLite prompt payloads.

The old design rewrote a 100MB+ pretty-printed state JSON every poll because
``known`` / ``backlog`` embedded full Comfy prompt + extra_data blobs. That
caused terabytes of Block I/O and high CPU.

Layout (next to ``comfy_queue_ledger_state.json``):
- ``comfy_queue_ledger_state.json`` — small meta (mode, snapshot ids, indexes)
- ``comfy_queue_ledger_payloads.sqlite`` — prompt_id → prompt/extra_data/outputs
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

PAYLOAD_KEYS = ("prompt", "extra_data", "outputs_to_execute")
KNOWN_META_KEYS = (
    "first_seen_ts",
    "first_seen_at",
    # last_seen_* intentionally omitted from disk meta: they change every poll and
    # would defeat dirty detection. In-memory state still tracks them.
    "last_phase",
)
BACKLOG_META_KEYS = ("prompt_id", "enqueued_backlog_ts", "source")


def default_payload_db_path(state_path: Path) -> Path:
    return Path(state_path).with_name("comfy_queue_ledger_payloads.sqlite")


def _utc_iso(ts: Optional[float] = None) -> str:
    t = float(time.time() if ts is None else ts)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _payload_hash(prompt: Any, extra_data: Any, outputs: Any) -> str:
    blob = _json_dumps({"prompt": prompt, "extra_data": extra_data, "outputs_to_execute": outputs})
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class LedgerPayloadStore:
    """SQLite sidecar for mirrored Comfy prompt payloads."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=30.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payloads (
                prompt_id TEXT PRIMARY KEY,
                prompt_json TEXT,
                extra_data_json TEXT,
                outputs_json TEXT,
                content_hash TEXT NOT NULL,
                updated_ts REAL NOT NULL
            )
            """
        )
        self._conn.commit()
        self._hash_cache: Dict[str, str] = {}
        self._load_hash_cache()

    def _load_hash_cache(self) -> None:
        cur = self._conn.execute("SELECT prompt_id, content_hash FROM payloads")
        self._hash_cache = {str(pid): str(h) for pid, h in cur.fetchall()}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def upsert(
        self,
        prompt_id: str,
        *,
        prompt: Any = None,
        extra_data: Any = None,
        outputs_to_execute: Any = None,
    ) -> bool:
        """Insert/update payload. Returns True if a DB write happened."""
        pid = str(prompt_id).strip()
        if not pid:
            return False
        h = _payload_hash(prompt, extra_data, outputs_to_execute)
        if self._hash_cache.get(pid) == h:
            return False
        now = float(time.time())
        self._conn.execute(
            """
            INSERT INTO payloads(prompt_id, prompt_json, extra_data_json, outputs_json, content_hash, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(prompt_id) DO UPDATE SET
                prompt_json=excluded.prompt_json,
                extra_data_json=excluded.extra_data_json,
                outputs_json=excluded.outputs_json,
                content_hash=excluded.content_hash,
                updated_ts=excluded.updated_ts
            """,
            (
                pid,
                _json_dumps(prompt) if prompt is not None else None,
                _json_dumps(extra_data) if extra_data is not None else None,
                _json_dumps(outputs_to_execute) if outputs_to_execute is not None else None,
                h,
                now,
            ),
        )
        self._conn.commit()
        self._hash_cache[pid] = h
        return True

    def get(self, prompt_id: str) -> Dict[str, Any]:
        pid = str(prompt_id).strip()
        cur = self._conn.execute(
            "SELECT prompt_json, extra_data_json, outputs_json FROM payloads WHERE prompt_id=?",
            (pid,),
        )
        row = cur.fetchone()
        if not row:
            return {}
        out: Dict[str, Any] = {}
        if row[0]:
            try:
                out["prompt"] = json.loads(row[0])
            except Exception:
                pass
        if row[1]:
            try:
                out["extra_data"] = json.loads(row[1])
            except Exception:
                pass
        if row[2]:
            try:
                out["outputs_to_execute"] = json.loads(row[2])
            except Exception:
                pass
        return out

    def delete_many(self, prompt_ids: Iterable[str]) -> int:
        ids = [str(x).strip() for x in prompt_ids if str(x).strip()]
        if not ids:
            return 0
        n = 0
        for pid in ids:
            cur = self._conn.execute("DELETE FROM payloads WHERE prompt_id=?", (pid,))
            n += int(cur.rowcount or 0)
            self._hash_cache.pop(pid, None)
        self._conn.commit()
        return n

    def purge_except(self, keep_ids: Set[str]) -> int:
        keep = {str(x).strip() for x in keep_ids if str(x).strip()}
        cur = self._conn.execute("SELECT prompt_id FROM payloads")
        drop = [str(r[0]) for r in cur.fetchall() if str(r[0]) not in keep]
        return self.delete_many(drop)

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM payloads")
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0


def _strip_known_for_disk(known: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for pid, rec in known.items():
        if not isinstance(rec, dict):
            continue
        slim = {k: rec[k] for k in KNOWN_META_KEYS if k in rec}
        out[str(pid)] = slim
    return out


def _strip_backlog_for_disk(backlog: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in backlog:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("prompt_id") or "").strip()
        if not pid:
            continue
        slim = {k: item[k] for k in BACKLOG_META_KEYS if k in item}
        slim["prompt_id"] = pid
        out.append(slim)
    return out


def _extract_payloads_to_store(state: Dict[str, Any], store: LedgerPayloadStore) -> int:
    """Move embedded payloads from in-memory/legacy state into SQLite. Returns upserts."""
    writes = 0
    known = state.get("known")
    if isinstance(known, dict):
        for pid, rec in known.items():
            if not isinstance(rec, dict):
                continue
            if not any(k in rec for k in PAYLOAD_KEYS):
                continue
            if store.upsert(
                str(pid),
                prompt=rec.get("prompt"),
                extra_data=rec.get("extra_data"),
                outputs_to_execute=rec.get("outputs_to_execute"),
            ):
                writes += 1
    backlog = state.get("backlog")
    if isinstance(backlog, list):
        for item in backlog:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("prompt_id") or "").strip()
            if not pid or not any(k in item for k in PAYLOAD_KEYS):
                continue
            if store.upsert(
                pid,
                prompt=item.get("prompt"),
                extra_data=item.get("extra_data"),
                outputs_to_execute=item.get("outputs_to_execute"),
            ):
                writes += 1
    return writes


def hydrate_state_payloads(state: Dict[str, Any], store: LedgerPayloadStore) -> None:
    """Attach SQLite payloads onto in-memory known/backlog records."""
    known = state.get("known")
    if isinstance(known, dict):
        for pid, rec in list(known.items()):
            if not isinstance(rec, dict):
                continue
            payload = store.get(str(pid))
            if payload:
                rec.update(payload)
                known[pid] = rec
    backlog = state.get("backlog")
    if isinstance(backlog, list):
        for item in backlog:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("prompt_id") or "").strip()
            if not pid:
                continue
            payload = store.get(pid)
            if payload:
                item.update(payload)


def state_has_embedded_payloads(state: Dict[str, Any]) -> bool:
    known = state.get("known")
    if isinstance(known, dict):
        for rec in known.values():
            if isinstance(rec, dict) and any(k in rec for k in PAYLOAD_KEYS):
                return True
    backlog = state.get("backlog")
    if isinstance(backlog, list):
        for item in backlog:
            if isinstance(item, dict) and any(k in item for k in PAYLOAD_KEYS):
                return True
    return False


def build_disk_meta(state: Dict[str, Any]) -> Dict[str, Any]:
    """Compact meta view suitable for frequent JSON rewrites."""
    meta = dict(state)
    known = meta.get("known")
    if isinstance(known, dict):
        meta["known"] = _strip_known_for_disk(known)
    backlog = meta.get("backlog")
    if isinstance(backlog, list):
        meta["backlog"] = _strip_backlog_for_disk(backlog)
    meta["payload_store"] = "sqlite"
    meta["updated_at"] = _utc_iso()
    return meta


def write_compact_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class LedgerStatePersister:
    """Persist ledger state with split payload store + dirty compact meta writes."""

    def __init__(self, state_path: Path, payload_db_path: Optional[Path] = None) -> None:
        self.state_path = Path(state_path)
        self.payload_db_path = Path(payload_db_path) if payload_db_path else default_payload_db_path(self.state_path)
        self.store = LedgerPayloadStore(self.payload_db_path)
        self._last_meta_hash: Optional[str] = None

    def close(self) -> None:
        self.store.close()

    def load(self, default_state_fn) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Load state, migrating legacy embedded payloads if needed.

        Returns ``(state, info)`` where info has migration counters.
        """
        info = {"migrated_payloads": 0, "legacy_embedded": False, "payload_rows": 0}
        if not self.state_path.exists():
            return default_state_fn(), info
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            if not raw.strip():
                raise ValueError("empty ledger state file")
            obj = json.loads(raw)
        except Exception:
            return default_state_fn(), info
        if not isinstance(obj, dict):
            return default_state_fn(), info
        state = default_state_fn()
        for k in state.keys():
            if k in obj:
                state[k] = obj[k]
        if not isinstance(state.get("known"), dict):
            state["known"] = {}
        if not isinstance(state.get("backlog"), list):
            state["backlog"] = []

        if state_has_embedded_payloads(state):
            info["legacy_embedded"] = True
            info["migrated_payloads"] = _extract_payloads_to_store(state, self.store)
            # Rewrite slim meta immediately so the fat file dies.
            self.persist(state, force=True)

        hydrate_state_payloads(state, self.store)
        info["payload_rows"] = self.store.count()
        # Seed dirty detector from current slim meta.
        meta = build_disk_meta(state)
        self._last_meta_hash = hashlib.sha256(_json_dumps(meta).encode("utf-8")).hexdigest()
        return state, info

    def persist(self, state: Dict[str, Any], *, force: bool = False) -> Dict[str, Any]:
        """Upsert changed payloads; rewrite meta JSON only when slim meta changed."""
        payload_writes = _extract_payloads_to_store(state, self.store)
        # Keep sqlite in sync with prune of known/backlog ids.
        keep: Set[str] = set()
        known = state.get("known")
        if isinstance(known, dict):
            keep.update(str(pid) for pid in known.keys())
        backlog = state.get("backlog")
        if isinstance(backlog, list):
            for item in backlog:
                if isinstance(item, dict) and item.get("prompt_id"):
                    keep.add(str(item["prompt_id"]))
        purged = self.store.purge_except(keep)

        meta = build_disk_meta(state)
        meta_hash = hashlib.sha256(_json_dumps(meta).encode("utf-8")).hexdigest()
        wrote_meta = False
        if force or meta_hash != self._last_meta_hash:
            write_compact_json(self.state_path, meta)
            self._last_meta_hash = meta_hash
            wrote_meta = True
        return {
            "payload_writes": payload_writes,
            "payload_purged": purged,
            "wrote_meta": wrote_meta,
            "meta_bytes": self.state_path.stat().st_size if self.state_path.exists() else 0,
        }
