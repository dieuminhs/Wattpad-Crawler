from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

BACKEND_NAME = "local-story-archive-desktop-backend"


def _bundle_args() -> list[str]:
    system = platform.system()
    if system == "Windows":
        return ["--bundles", "nsis"]
    if system == "Darwin":
        return ["--bundles", "app,dmg"]
    return []

def _backend_exe_name() -> str:
    return f"{BACKEND_NAME}.exe" if platform.system() == "Windows" else BACKEND_NAME

def _sign_macos_backend(project_root: Path) -> None:
    if platform.system() != "Darwin":
        return
    signing_identity = os.environ.get("APPLE_SIGNING_IDENTITY", "").strip()
    if not signing_identity:
        print("Skipping macOS backend signing: APPLE_SIGNING_IDENTITY is not set.")
        return

    backend_path = project_root / "src-tauri" / "bin" / _backend_exe_name()
    if not backend_path.exists():
        raise FileNotFoundError(f"macOS backend executable was not built: {backend_path}")

    subprocess.run(
        [
            "codesign",
            "--force",
            "--options",
            "runtime",
            "--timestamp",
            "--sign",
            signing_identity,
            str(backend_path),
        ],
        cwd=project_root,
        check=True,
    )
    subprocess.run(
        ["codesign", "--verify", "--verbose=2", str(backend_path)],
        cwd=project_root,
        check=True,
    )


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
    _sign_macos_backend(project_root)
    subprocess.run([str(tauri), "build", *_bundle_args()], cwd=project_root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
