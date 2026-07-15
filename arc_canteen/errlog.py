"""Client-side error log: ~/.arc-canteen/error.log (0600).

Console errors vanish with the terminal; this file keeps the last
~128KB of them so "it failed earlier" is diagnosable after the fact.
Append-only, best-effort — logging must never break the CLI, so every
failure here is swallowed.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from . import paths

ERROR_LOG = paths.ARC_DIR / "error.log"
_MAX_BYTES = 256 * 1024  # trim to the newest half when exceeded


def log(context: str, message: str) -> None:
    try:
        paths.ensure_dir()
        if ERROR_LOG.exists() and ERROR_LOG.stat().st_size > _MAX_BYTES:
            tail = ERROR_LOG.read_bytes()[-_MAX_BYTES // 2:]
            with paths.secure_open(ERROR_LOG) as f:
                f.write(tail.decode(errors="replace"))
        line = f"{datetime.now(timezone.utc).isoformat()}  [{context}] {message}\n"
        fd = os.open(ERROR_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(line)
        try:
            ERROR_LOG.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass
