"""Filesystem helpers for the per-user ~/.arc-canteen state directory.

Everything the CLI persists here is private to the user — auth tokens,
the token-bearing RPC URL, queued events carrying your handles and
free-text updates — so the directory is created 0700 and the files 0600.
The mode is re-applied on every write so a directory or file left behind
by an older version (or created by hand) gets tightened too. chmod is
best-effort: on a filesystem that doesn't support it we leave the mode
alone rather than crash the CLI.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

ARC_DIR = Path.home() / ".arc-canteen"

DIR_MODE = 0o700
FILE_MODE = 0o600


def ensure_dir(path: Path = ARC_DIR) -> Path:
    """mkdir -p ``path``, then chmod it to 0700 (best-effort)."""
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(DIR_MODE)
    except OSError:
        pass
    return path


@contextmanager
def secure_open(path: Path) -> Iterator[TextIO]:
    """``open(path, "w")`` with the parent dir forced to 0700 and the file
    created — and left — at 0600. The file is created via ``os.open`` with
    mode 0600 so there's no window where it's world-readable; the trailing
    chmod also normalises a pre-existing file. Use as a context manager."""
    ensure_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        with os.fdopen(fd, "w") as f:
            yield f
    finally:
        try:
            path.chmod(FILE_MODE)
        except OSError:
            pass


def secure_write_text(path: Path, text: str) -> None:
    """``Path.write_text`` equivalent honouring the 0700/0600 policy."""
    with secure_open(path) as f:
        f.write(text)
