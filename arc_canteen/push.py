"""arc-canteen push — sync local event queue to the remote server.

queue.yaml is append-only. Events are never removed.
pushed_at is null until the event is successfully delivered; once set it
is never cleared. The server is intentionally idempotent — re-sending an
event that already landed is harmless.
"""

from __future__ import annotations

import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from . import config

SERVER_URL = "https://arc-cli-server.thecanteenapp.com"
QUEUE_FILE = Path.home() / ".arc-canteen" / "queue.yaml"
_TIMEOUT = 4
_PING_TIMEOUT = 2

# Set once per process by ping(). If False, all network calls are skipped
# for the remainder of this CLI invocation.
_server_up: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_queue() -> list[dict]:
    if not QUEUE_FILE.exists():
        return []
    with open(QUEUE_FILE) as f:
        return yaml.safe_load(f) or []


def _save_queue(items: list[dict]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_FILE, "w") as f:
        yaml.dump(items, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _pending(queue: list[dict]) -> list[dict]:
    """Events that have not yet been successfully pushed."""
    return [e for e in queue if not e.get("pushed_at")]


def _send(events: list[dict]) -> bool:
    """POST a batch of events to the server. Returns True on success."""
    if not _server_up:
        return False
    token = config.get("auth.server_token")
    if not token:
        return False
    payload = [{k: v for k, v in e.items() if k != "pushed_at"} for e in events]
    try:
        resp = httpx.post(
            f"{SERVER_URL}/events",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        return resp.status_code == 200
    except (httpx.RequestError, httpx.TimeoutException):
        return False


def _mark_pushed(queue: list[dict]) -> list[dict]:
    """Return a new queue with pushed_at stamped on every currently-pending event."""
    now = _now()
    return [{**e, "pushed_at": now} if not e.get("pushed_at") else e for e in queue]


def ping() -> bool:
    """
    Check if the server is reachable. Called once at startup.
    Sets _server_up for the rest of this process — if False, all push
    calls are skipped silently so the CLI never blocks on a dead server.
    """
    global _server_up
    try:
        resp = httpx.get(f"{SERVER_URL}/ping", timeout=_PING_TIMEOUT)
        _server_up = resp.status_code == 200
    except (httpx.RequestError, httpx.TimeoutException):
        _server_up = False
    return _server_up


def pending_count() -> int:
    """Number of events not yet pushed to the server."""
    return len(_pending(_load_queue()))


def drain_queue() -> None:
    """Flush unpushed events to the server. No-op if server is down."""
    if not _server_up:
        return
    queue = _load_queue()
    pending = _pending(queue)
    if not pending:
        return
    if _send(pending):
        _save_queue(_mark_pushed(queue))


def push_event(event_type: str, data: dict[str, Any] | None = None) -> None:
    """
    Append an event to queue.yaml and attempt to push all pending events.
    pushed_at marks successful delivery; events are never removed from the file.
    If the server is down this invocation, the event is queued silently.
    """
    event: dict[str, Any] = {
        "type": event_type,
        "occurred_at": _now(),
        "pushed_at": None,
        **(data or {}),
    }

    queue = _load_queue()
    queue.append(event)

    if _server_up and _send(_pending(queue)):
        queue = _mark_pushed(queue)

    _save_queue(queue)


def cli_login(github_token: str) -> bool:
    """
    Exchange a GitHub access token for a server bearer token.
    Stores the server token in config on success.
    Server being down is not fatal — local login still succeeds.
    """
    if not _server_up:
        return False
    try:
        resp = httpx.post(
            f"{SERVER_URL}/auth/cli-login",
            json={"github_token": github_token},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return False
        data = resp.json()
        server_token = data.get("server_token")
        if not server_token:
            return False
        config.set_val("auth.server_token", server_token)
        return True
    except (httpx.RequestError, httpx.TimeoutException):
        return False
