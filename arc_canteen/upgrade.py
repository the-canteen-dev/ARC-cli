"""Check GitHub for a newer tag than what's installed; nudge the user
to upgrade via `uv tool upgrade arc-canteen`.

Cached for 1 hour in ~/.arc-canteen/version_check.yaml so we don't hit
GitHub's API on every CLI invocation. Non-blocking and silent on any
failure: a network hiccup never prevents the CLI from running.
"""

from __future__ import annotations

import re
import time
import yaml
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import httpx
from rich.console import Console

console = Console()

REPO = "the-canteen-dev/ARC-cli"
CACHE_FILE = Path.home() / ".arc-canteen" / "version_check.yaml"
CACHE_TTL_SECONDS = 60 * 60   # 1 hour
_TIMEOUT = 2


def _parse(s: str) -> tuple[int, ...] | None:
    """Lenient semver-ish parse. Strips leading 'v', splits on .-+,
    converts each piece to int until a non-numeric piece is hit.
    Returns None if no numeric pieces."""
    s = s.lstrip("vV").strip()
    if not s:
        return None
    out: list[int] = []
    for p in re.split(r"[.\-+]", s):
        try:
            out.append(int(p))
        except ValueError:
            break
    return tuple(out) if out else None


def _installed_version() -> tuple[int, ...] | None:
    try:
        return _parse(metadata.version("arc-canteen"))
    except metadata.PackageNotFoundError:
        return None


def _load_cache() -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE) as f:
            return yaml.safe_load(f) or None
    except (OSError, yaml.YAMLError):
        return None


def _save_cache(data: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CACHE_FILE, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    except OSError:
        pass


def _fetch_latest_tag() -> str | None:
    """Return the highest-versioned tag name on the GitHub repo, or None
    on network/API failure or if there are no parseable tags."""
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{REPO}/tags",
            headers={"Accept": "application/vnd.github+json"},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        tags = resp.json()
    except (httpx.RequestError, httpx.TimeoutException, ValueError):
        return None

    if not isinstance(tags, list):
        return None

    best_name: str | None = None
    best_ver: tuple[int, ...] | None = None
    for tag in tags:
        name = tag.get("name") if isinstance(tag, dict) else None
        if not name:
            continue
        ver = _parse(name)
        if ver is None:
            continue
        if best_ver is None or ver > best_ver:
            best_name, best_ver = name, ver
    return best_name


def maybe_print_upgrade_notice() -> None:
    """Print a yellow nudge if a newer tag is published. No-op otherwise."""
    installed = _installed_version()
    if installed is None:
        return

    cache = _load_cache()
    now = time.time()
    latest_name: str | None = None
    fresh = (
        cache
        and isinstance(cache.get("checked_at_unix"), (int, float))
        and (now - float(cache["checked_at_unix"])) < CACHE_TTL_SECONDS
    )
    if fresh:
        latest_name = cache.get("latest_tag") or None
    else:
        latest_name = _fetch_latest_tag()
        _save_cache({
            "checked_at_unix": now,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "latest_tag": latest_name or "",
        })

    if not latest_name:
        return

    latest_ver = _parse(latest_name)
    if latest_ver is None or latest_ver <= installed:
        return

    installed_str = ".".join(str(p) for p in installed)
    console.print(
        f"[yellow]A newer arc-canteen is available: "
        f"{installed_str} → [bold]{latest_name}[/bold][/yellow]\n"
        f"[dim]Run [bold cyan]uv tool upgrade arc-canteen[/bold cyan] to upgrade.[/dim]"
    )
