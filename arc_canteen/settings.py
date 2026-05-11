"""User-editable settings at ~/.arc-canteen/settings.yaml.

Two keys today:
  chain       — 'testnet' or 'mainnet'  (defaults to testnet)
  event_name  — short string label      (defaults to 'agora')

These are merged into every event the CLI sends to the server, so the
server-side event log records which chain and which event the action
was for.

Hardening against malicious / accidental large files:
  - File is rejected outright if it's larger than MAX_BYTES (10 KB).
  - After parse, the result must be a flat mapping — any nested dict
    or list value is rejected. Prevents YAML/JSON bomb shapes from
    making it past load().
  - Unknown chains are rejected so 'event_name: ../etc/passwd' style
    tricks don't sneak through (event_name is still free-form, but
    capped at 64 chars).
"""

from __future__ import annotations

import yaml
from pathlib import Path

SETTINGS_FILE = Path.home() / ".arc-canteen" / "settings.yaml"

MAX_BYTES = 10 * 1024              # 10 KB file-size ceiling
ALLOWED_CHAINS = ("testnet", "mainnet")
MAX_EVENT_NAME_LEN = 64
DEFAULTS: dict = {
    "chain": "testnet",
    "event_name": "agora",
}


class SettingsError(RuntimeError):
    """Raised when settings.yaml is malformed or too large."""


def _validate(raw: dict) -> dict:
    """Apply defaults, validate schema. Returns the effective settings."""
    if not isinstance(raw, dict):
        raise SettingsError("settings.yaml must be a top-level mapping")

    # Flat-only: reject any nested dict or list value.
    for k, v in raw.items():
        if isinstance(v, (dict, list)):
            raise SettingsError(
                f"settings.{k} must be a scalar — nested values are not allowed"
            )

    merged = {**DEFAULTS, **raw}

    chain = merged.get("chain")
    if chain not in ALLOWED_CHAINS:
        raise SettingsError(
            f"settings.chain must be one of {ALLOWED_CHAINS}, got {chain!r}"
        )

    ev = merged.get("event_name")
    if not isinstance(ev, str) or not ev:
        raise SettingsError("settings.event_name must be a non-empty string")
    if len(ev) > MAX_EVENT_NAME_LEN:
        raise SettingsError(
            f"settings.event_name exceeds {MAX_EVENT_NAME_LEN} chars"
        )

    return merged


def _write_defaults() -> dict:
    """Create the settings file with default values, return them."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        yaml.safe_dump(DEFAULTS, f, default_flow_style=False, sort_keys=False)
    return dict(DEFAULTS)


def load() -> dict:
    """Read, validate, and return the merged settings.

    Creates the file with defaults on first invocation. Raises
    SettingsError on malformed input — callers in the hot path should
    either catch this or let it propagate as a clear startup failure.
    """
    if not SETTINGS_FILE.exists():
        return _write_defaults()

    size = SETTINGS_FILE.stat().st_size
    if size > MAX_BYTES:
        raise SettingsError(
            f"settings.yaml is {size} bytes; max is {MAX_BYTES}"
        )

    with open(SETTINGS_FILE) as f:
        raw = yaml.safe_load(f) or {}

    return _validate(raw)
