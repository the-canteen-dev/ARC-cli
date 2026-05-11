"""Manage the developer-docs + sample-codebase bundle at
`~/.arc-canteen/context/`. The CLI command thin-wraps these helpers.

The upstream repo is github.com/the-canteen-dev/context-arc. It carries
`AGENTS.md` as its agent-facing entry point, a `docs/` tree mirroring
upstream Arc + Circle markdown, and a `samples/` tree of git submodules
pointing at the reference codebases.

`arc-canteen context sync` clones (first time) or pulls (subsequent),
always with `--recurse-submodules` so the sample codebases come along.

`arc-canteen context` (no sub) dumps AGENTS.md + a flat path manifest
to stdout — the manifest lists every doc file and every sample
directory by path. Agents can then read specific files directly off
disk via their own file tools.

`arc-canteen context --full` additionally inlines the content of every
.md / .yaml under docs/, for stateless agent runtimes that don't have
file-system access.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import paths

CONTEXT_DIR = paths.ARC_DIR / "context"
CONTEXT_REPO_URL = "https://github.com/the-canteen-dev/context-arc.git"


class ContextError(RuntimeError):
    """sync or read failure."""


def is_synced() -> bool:
    return (CONTEXT_DIR / "AGENTS.md").exists()


def sync() -> None:
    """Clone or pull the context repo into CONTEXT_DIR, including submodules."""
    paths.ensure_dir()  # CONTEXT_DIR.parent is ~/.arc-canteen — keep it 0700
    if (CONTEXT_DIR / ".git").exists():
        # update in place
        try:
            subprocess.run(
                ["git", "-C", str(CONTEXT_DIR), "pull", "--recurse-submodules", "--quiet"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(CONTEXT_DIR), "submodule", "update", "--init", "--recursive", "--quiet"],
                check=True,
            )
        except subprocess.CalledProcessError as e:
            raise ContextError(f"git pull failed: {e}")
        return

    if CONTEXT_DIR.exists() and any(CONTEXT_DIR.iterdir()):
        raise ContextError(
            f"{CONTEXT_DIR} exists and is non-empty but isn't a git checkout; "
            f"move or remove it and re-run"
        )

    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--recursive", CONTEXT_REPO_URL, str(CONTEXT_DIR)],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise ContextError(f"git clone failed: {e}")


def _is_submodule_internal(p: Path) -> bool:
    """Skip files inside a sample submodule's working tree (we list
    the submodule root path instead)."""
    parts = p.relative_to(CONTEXT_DIR).parts
    return len(parts) > 2 and parts[0] == "samples"


def list_paths() -> list[str]:
    """Return relative paths of all docs files + each sample submodule's
    root directory. Sorted, no .git entries."""
    if not is_synced():
        raise ContextError("context not synced; run `arc-canteen context sync` first")

    out: list[str] = []

    # Top-level docs
    for p in sorted((CONTEXT_DIR / "docs").rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            out.append(str(p.relative_to(CONTEXT_DIR)))

    # Each sample submodule represented by its root dir
    samples = CONTEXT_DIR / "samples"
    if samples.is_dir():
        for d in sorted(samples.iterdir()):
            if d.is_dir() and d.name != ".git":
                out.append(f"{d.relative_to(CONTEXT_DIR)}/")

    # AGENTS.md + README.md at root
    for name in ("AGENTS.md", "README.md"):
        p = CONTEXT_DIR / name
        if p.exists():
            out.insert(0 if name == "AGENTS.md" else 1, name)

    return out


def read_entry() -> str:
    """Return AGENTS.md content, or '' if not synced."""
    p = CONTEXT_DIR / "AGENTS.md"
    return p.read_text() if p.exists() else ""


def iter_doc_contents():
    """Yield (relative_path, content) for every .md/.yaml/.txt under docs/.
    Submodule sample contents are not included — they're listed as paths
    in list_paths() so callers can read them on demand.
    """
    if not is_synced():
        return
    for p in sorted((CONTEXT_DIR / "docs").rglob("*")):
        if not p.is_file():
            continue
        if ".git" in p.parts:
            continue
        if p.suffix not in (".md", ".yaml", ".txt"):
            continue
        try:
            yield (str(p.relative_to(CONTEXT_DIR)), p.read_text())
        except UnicodeDecodeError:
            continue
