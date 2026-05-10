"""Local YAML-based storage for arc-canteen CLI state."""

import re
import yaml
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".arc-canteen"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

# PyYAML auto-parses ISO datetime strings into datetime objects on load.
# This custom dumper quotes them so they stay as plain strings.
_ISO_RE = re.compile(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}')

class _Dumper(yaml.Dumper):
    pass

def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    style = "'" if _ISO_RE.match(data) else None
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)

_Dumper.add_representer(str, _str_representer)


def load() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f) or {}


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.dump(data, f, Dumper=_Dumper, default_flow_style=False, allow_unicode=True, sort_keys=False)


def get(key: str, default: Any = None) -> Any:
    """Dot-notation key access, e.g. 'auth.github_handle'."""
    cfg = load()
    for k in key.split("."):
        if not isinstance(cfg, dict):
            return default
        cfg = cfg.get(k)
        if cfg is None:
            return default
    return cfg


def set_val(key: str, value: Any) -> None:
    """Set a dot-notation key."""
    cfg = load()
    keys = key.split(".")
    d = cfg
    for k in keys[:-1]:
        if k not in d or not isinstance(d[k], dict):
            d[k] = {}
        d = d[k]
    d[keys[-1]] = value
    save(cfg)


def is_logged_in() -> bool:
    return bool(get("auth.github_token"))


def has_discord() -> bool:
    return bool(get("profile.discord"))
