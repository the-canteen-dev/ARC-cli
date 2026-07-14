"""Build hook: bake the git commit hash into the package so installed
copies (PyPI wheels, `uv tool install git+…`) can report which commit
they were built from — the version string alone can't distinguish two
builds that both say 0.1.12.

Writes arc_canteen/_build_info.py and force-includes it (it's gitignored,
which hatchling would otherwise honor). `uv build` builds the wheel FROM
the sdist, where there's no .git to ask — so the hash is captured when
the sdist is built from the checkout, and a later git-less build keeps
the already-baked value rather than clobbering it with "".
"""

from __future__ import annotations

import os
import subprocess

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BuildInfoHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        path = os.path.join(self.root, "arc_canteen", "_build_info.py")

        commit = ""
        try:
            out = subprocess.run(
                ["git", "-C", self.root, "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0:
                commit = out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass

        if commit or not os.path.exists(path):
            with open(path, "w") as f:
                f.write(f'COMMIT = "{commit}"\n')

        build_data.setdefault("force_include", {})[path] = "arc_canteen/_build_info.py"
