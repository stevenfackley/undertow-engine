from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    cache_root = repo_root / ".cache" / "pytest"
    base_temp = cache_root / "temp"
    cache_dir = cache_root / "cache"
    tmp_dir = cache_root / "tmp"

    for path in (base_temp, cache_dir, tmp_dir):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["TMP"] = str(tmp_dir)
    env["TEMP"] = str(tmp_dir)
    env["TMPDIR"] = str(tmp_dir)

    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "--basetemp",
        str(base_temp),
        "-o",
        f"cache_dir={cache_dir}",
    ]

    return subprocess.run(command, cwd=repo_root, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
