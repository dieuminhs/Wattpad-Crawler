from pathlib import Path
from unittest.mock import Mock

from scripts import build_desktop

def test_sign_macos_backend_skips_without_identity(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build_desktop.platform, "system", lambda: "Darwin")
    monkeypatch.delenv("APPLE_SIGNING_IDENTITY", raising=False)
    monkeypatch.setattr(build_desktop.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    build_desktop._sign_macos_backend(tmp_path)

    assert calls == []

def test_sign_macos_backend_signs_bundled_helper(tmp_path, monkeypatch):
    backend_path = tmp_path / "src-tauri" / "bin" / "local-story-archive-desktop-backend"
    backend_path.parent.mkdir(parents=True)
    backend_path.write_text("backend", encoding="utf-8")
    run = Mock()
    monkeypatch.setattr(build_desktop.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("APPLE_SIGNING_IDENTITY", "Developer ID Application: Example (TEAMID)")
    monkeypatch.setattr(build_desktop.subprocess, "run", run)

    build_desktop._sign_macos_backend(tmp_path)

    sign_command = run.call_args_list[0].args[0]
    verify_command = run.call_args_list[1].args[0]
    assert sign_command[:6] == [
        "codesign",
        "--force",
        "--options",
        "runtime",
        "--timestamp",
        "--sign",
    ]
    assert sign_command[6] == "Developer ID Application: Example (TEAMID)"
    assert Path(sign_command[-1]) == backend_path
    assert verify_command[:3] == ["codesign", "--verify", "--verbose=2"]
    assert Path(verify_command[-1]) == backend_path
