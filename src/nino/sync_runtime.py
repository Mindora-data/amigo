from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _script_path() -> Path:
    candidates = []
    root_env = os.environ.get("NINO_SYNC_SCRIPT_ROOT", "").strip()
    if root_env:
        candidates.append(Path(root_env) / "scripts" / "nino-sync-runtime")
    candidates.append(Path.cwd() / "scripts" / "nino-sync-runtime")
    candidates.append(Path(__file__).resolve().parents[2] / "scripts" / "nino-sync-runtime")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def main(argv: list[str] | None = None) -> int:
    script = _script_path()
    args = [str(script), *(argv if argv is not None else sys.argv[1:])]
    return subprocess.call(args)


if __name__ == "__main__":
    raise SystemExit(main())
