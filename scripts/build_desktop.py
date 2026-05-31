from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path


def _bundle_args() -> list[str]:
    system = platform.system()
    if system == "Windows":
        return ["--bundles", "nsis"]
    if system == "Darwin":
        return ["--bundles", "app,dmg"]
    return []


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    tauri = project_root / "node_modules" / ".bin" / (
        "tauri.cmd" if platform.system() == "Windows" else "tauri"
    )
    subprocess.run(
        [sys.executable, "scripts/build_desktop_backend.py", "--clean"],
        cwd=project_root,
        check=True,
    )
    subprocess.run([str(tauri), "build", *_bundle_args()], cwd=project_root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
