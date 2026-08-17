#!/usr/bin/env python3
"""Gentle capped restart for ComfyUI (compose up), without Docker restart: unless-stopped.

See scripts/install-systemd-boot.sh (comfyui-runpod-keep.timer).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

MAX_ATTEMPTS = 3
WINDOW = timedelta(hours=2)
MIN_GAP = timedelta(minutes=5)
OOM_GAP = timedelta(minutes=15)
STARTUP_GRACE = timedelta(minutes=12)

BOOT_UNIT = "comfyui-runpod-docker.service"
COMFY_CONTAINER = "comfyui0-runpod"
WATCH_CONTAINER = "comfyui0-watch-queue"

Action = Literal["noop", "up"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_docker_time(value: str | None) -> datetime | None:
    if not value or value.startswith("0001-01-01"):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "." in text:
        head, rest = text.split(".", 1)
        sign_at = None
        for i, ch in enumerate(rest):
            if ch in "+-" and i > 0:
                sign_at = i
                break
        if sign_at is None:
            frac, tz = rest, "+00:00"
        else:
            frac, tz = rest[:sign_at], rest[sign_at:]
        frac = "".join(c for c in frac if c.isdigit())[:6].ljust(6, "0")
        text = f"{head}.{frac}{tz}"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class KeepState:
    attempts: list[datetime] = field(default_factory=list)

    def prune(self, now: datetime) -> None:
        cutoff = now - WINDOW
        self.attempts = [t for t in self.attempts if t > cutoff]

    def to_json(self) -> dict[str, Any]:
        return {"attempts": [iso(t) for t in self.attempts]}

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> KeepState:
        raw = (data or {}).get("attempts") or []
        attempts: list[datetime] = []
        for item in raw:
            dt = parse_docker_time(str(item))
            if dt is not None:
                attempts.append(dt)
        return cls(attempts=attempts)


@dataclass
class ContainerFacts:
    present: bool
    status: str = ""
    exit_code: int | None = None
    oom_killed: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class Facts:
    now: datetime
    boot_unit_active: bool
    hold: bool
    preflight_ok: bool
    comfy: ContainerFacts
    watch_queue: ContainerFacts
    http_8188_ok: bool


@dataclass
class Decision:
    action: Action
    reason: str
    services: tuple[str, ...] = ()
    attempts_in_window: int = 0


def decide(facts: Facts, state: KeepState) -> Decision:
    state.prune(facts.now)
    n = len(state.attempts)

    if facts.hold:
        return Decision("noop", "hold file present", attempts_in_window=n)
    if not facts.boot_unit_active:
        return Decision("noop", "boot unit inactive (stack stopped)", attempts_in_window=n)
    if not facts.preflight_ok:
        return Decision("noop", "preflight failed (docker or bind paths)", attempts_in_window=n)

    c = facts.comfy
    if c.present and c.status == "running":
        if facts.http_8188_ok:
            return Decision("noop", "comfyui running and /queue ok", attempts_in_window=n)
        if c.started_at and facts.now - c.started_at < STARTUP_GRACE:
            return Decision("noop", "startup grace (8188 not ready yet)", attempts_in_window=n)
        return Decision(
            "noop",
            "container running but 8188 down past grace (not killing)",
            attempts_in_window=n,
        )

    unexpected = (not c.present) or c.status in ("exited", "dead", "created")
    if not unexpected:
        return Decision("noop", f"status={c.status or 'unknown'} (no action)", attempts_in_window=n)

    if c.present and c.status == "exited" and (c.exit_code or 0) == 0 and not c.oom_killed:
        return Decision("noop", "clean exit 0 (not retrying)", attempts_in_window=n)

    if n >= MAX_ATTEMPTS:
        oldest = min(state.attempts)
        until = oldest + WINDOW
        return Decision(
            "noop",
            f"retry cap ({MAX_ATTEMPTS} in {int(WINDOW.total_seconds() // 3600)}h) until {iso(until)}",
            attempts_in_window=n,
        )

    if state.attempts:
        last = max(state.attempts)
        if facts.now - last < MIN_GAP:
            return Decision("noop", "min gap since last attempt", attempts_in_window=n)

    if c.oom_killed and c.finished_at and facts.now - c.finished_at < OOM_GAP:
        return Decision("noop", "OOM cooldown", attempts_in_window=n)

    services = ["comfyui"]
    w = facts.watch_queue
    if (not w.present) or w.status in ("exited", "dead"):
        services.append("watch_queue")

    return Decision("up", "unexpected exit; compose up", tuple(services), attempts_in_window=n)


def load_state(path: Path) -> KeepState:
    if not path.is_file():
        return KeepState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return KeepState()
    if not isinstance(data, dict):
        return KeepState()
    return KeepState.from_json(data)


def save_state(path: Path, state: KeepState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_json(), indent=2) + "\n", encoding="utf-8")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def boot_unit_active() -> bool:
    r = _run(["systemctl", "--user", "is-active", BOOT_UNIT], timeout=10)
    return r.stdout.strip() == "active"


def preflight_ok(root: Path) -> bool:
    script = root / "scripts" / "wait_for_compose_boot.sh"
    r = _run(["bash", str(script), "--check-only"], timeout=30)
    return r.returncode == 0


def inspect_container(name: str) -> ContainerFacts:
    fmt = (
        "{{.State.Status}}\t{{.State.ExitCode}}\t{{.State.OOMKilled}}"
        "\t{{.State.StartedAt}}\t{{.State.FinishedAt}}"
    )
    r = _run(["docker", "inspect", "-f", fmt, name], timeout=15)
    if r.returncode != 0:
        return ContainerFacts(present=False)
    parts = (r.stdout or "").strip().split("\t")
    if len(parts) < 5:
        return ContainerFacts(present=False)
    status, exit_s, oom_s, started, finished = parts[:5]
    try:
        exit_code = int(exit_s)
    except ValueError:
        exit_code = None
    return ContainerFacts(
        present=True,
        status=status.strip(),
        exit_code=exit_code,
        oom_killed=oom_s.strip().lower() == "true",
        started_at=parse_docker_time(started),
        finished_at=parse_docker_time(finished),
    )


def http_queue_ok() -> bool:
    r = _run(
        ["curl", "-fsS", "-m", "2", "http://127.0.0.1:8188/queue"],
        timeout=8,
    )
    return r.returncode == 0


def compose_up(root: Path, services: tuple[str, ...]) -> int:
    cmd = [
        "docker",
        "compose",
        "--env-file",
        ".env",
        "-f",
        "docker-compose.yml",
        "-f",
        "docker-compose.output-sftp.yml",
        "up",
        "-d",
        *services,
    ]
    r = subprocess.run(cmd, cwd=str(root), check=False)
    return r.returncode


def log_line(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = iso(_utc_now())
    line = f"{stamp} {message}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capped ComfyUI compose-up keeper")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo", type=Path, default=None)
    args = parser.parse_args(argv)

    root = (args.repo or repo_root()).resolve()
    data = root / ".data"
    state_path = data / "comfyui-keep.json"
    log_path = data / "comfyui-keep.log"
    hold_path = data / "comfyui.hold"

    now = _utc_now()
    state = load_state(state_path)
    facts = Facts(
        now=now,
        boot_unit_active=boot_unit_active(),
        hold=hold_path.is_file(),
        preflight_ok=preflight_ok(root),
        comfy=inspect_container(COMFY_CONTAINER),
        watch_queue=inspect_container(WATCH_CONTAINER),
        http_8188_ok=http_queue_ok(),
    )
    decision = decide(facts, state)
    extra = (
        f"action={decision.action} services={','.join(decision.services) or '-'} "
        f"attempts={decision.attempts_in_window} "
        f"comfy={facts.comfy.status or 'missing'} exit={facts.comfy.exit_code} "
        f"oom={facts.comfy.oom_killed}"
    )
    log_line(log_path, f"{decision.reason} ({extra})")

    if decision.action != "up":
        return 0
    if args.dry_run:
        return 0

    rc = compose_up(root, decision.services)
    if rc == 0:
        state.attempts.append(now)
        state.prune(now)
        save_state(state_path, state)
        log_line(log_path, f"compose up -d {' '.join(decision.services)} ok")
        return 0
    log_line(log_path, f"compose up failed rc={rc} (attempt not counted)")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
