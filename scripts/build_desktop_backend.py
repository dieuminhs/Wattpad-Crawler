from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND_NAME = "local-story-archive-desktop-backend"


def _exe_name(name: str = BACKEND_NAME) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the Local Story Archive desktop backend executable with PyInstaller.",
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=Path("src-tauri") / "bin",
        help="Directory where the Tauri wrapper should find the backend executable.",
    )
    parser.add_argument("--clean", action="store_true", help="Remove PyInstaller build output first.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_root = Path(__file__).resolve().parents[1]
    build_dir = project_root / "build" / "desktop-backend"
    pyinstaller_dist = build_dir / "dist"
    entrypoint = build_dir / "desktop_backend_entry.py"
    output_dir = (project_root / args.dist_dir).resolve()

    if args.clean:
        shutil.rmtree(build_dir, ignore_errors=True)

    build_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text(
        "from local_story_archive.desktop import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name",
        BACKEND_NAME,
        "--distpath",
        str(pyinstaller_dist),
        "--workpath",
        str(build_dir / "work"),
        "--specpath",
        str(build_dir),
        "--collect-data",
        "local_story_archive",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "sse_starlette",
        "--hidden-import",
        "multipart",
        "--hidden-import",
        "python_multipart",
        str(entrypoint),
    ]
    if platform.system() == "Windows":
        command.insert(4, "--windowed")
    if args.clean:
        command.insert(4, "--clean")

    subprocess.run(command, cwd=project_root, check=True)

    built_exe = pyinstaller_dist / _exe_name()
    target_exe = output_dir / _exe_name()
    shutil.copy2(built_exe, target_exe)
    print(f"Built desktop backend: {target_exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




