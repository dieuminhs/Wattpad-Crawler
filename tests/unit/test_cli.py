from pathlib import Path

import httpx
import pytest

from wattpad_crawler.auth import AuthError
from wattpad_crawler.cli import build_parser, main
from wattpad_crawler.jobs import ResolveError


def test_parser_has_expected_subcommands():
    parser = build_parser()
    args = parser.parse_args(["story", "123"])
    assert args.cmd == "story"
    assert args.target == "123"


def test_parser_library_command():
    parser = build_parser()
    args = parser.parse_args(["library", "--user", "alice"])
    assert args.cmd == "library"
    assert args.user == "alice"


def test_parser_list_command():
    parser = build_parser()
    args = parser.parse_args(["list", "L1"])
    assert args.cmd == "list"
    assert args.list_id == "L1"


def test_parser_url_command():
    parser = build_parser()
    args = parser.parse_args(["url", "https://www.wattpad.com/story/42-foo"])
    assert args.cmd == "url"
    assert args.target == "https://www.wattpad.com/story/42-foo"


def test_parser_status_command():
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.cmd == "status"


def test_parser_reset_command():
    parser = build_parser()
    args = parser.parse_args(["reset", "123"])
    assert args.cmd == "reset"
    assert args.target == "123"


def test_parser_default_output_dir():
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.output == Path("./wattpad-archive")


def test_parser_custom_output_dir():
    parser = build_parser()
    args = parser.parse_args(["--output", "/tmp/x", "status"])
    assert args.output == Path("/tmp/x")


def test_parser_requires_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_main_invalid_config_exits_2_without_traceback(output_dir, capsys):
    (output_dir / "_config.toml").write_text('cookie = "unterminated\n', encoding="utf-8")

    rc = main(["--output", str(output_dir), "status"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "ConfigError" in captured.err
    assert "_config.toml" in captured.err
    assert "Traceback" not in captured.err


def test_main_story_calls_archive_story(output_dir, monkeypatch):
    monkeypatch.setattr("wattpad_crawler.cli.validate_cookie", lambda client: None)
    captured = {}

    def fake_archive_story(cfg, client, manifest, sid, deps=None):
        captured["sid"] = sid
        captured["out"] = cfg.output_dir

    monkeypatch.setattr("wattpad_crawler.cli.archive_story", fake_archive_story)
    rc = main(["--output", str(output_dir), "story", "123456"])
    assert rc == 0
    assert captured["sid"] == "123456"
    assert captured["out"] == output_dir


def test_main_url_command_resolves_then_archives(output_dir, monkeypatch):
    monkeypatch.setattr("wattpad_crawler.cli.validate_cookie", lambda client: None)
    captured = {}

    def fake_archive_story(cfg, client, manifest, sid, deps=None):
        captured["sid"] = sid

    monkeypatch.setattr("wattpad_crawler.cli.archive_story", fake_archive_story)
    rc = main([
        "--output", str(output_dir),
        "url", "https://www.wattpad.com/story/789-foo-bar",
    ])
    assert rc == 0
    assert captured["sid"] == "789"


def test_main_url_command_accepts_numeric_wattpad_paths(output_dir, monkeypatch):
    captured = {}

    def fake_archive_story(cfg, client, manifest, sid, deps=None):
        captured["sid"] = sid

    monkeypatch.setattr(
        "wattpad_crawler.cli.resolve_url_story_id",
        lambda client, target: "123456789",
    )
    monkeypatch.setattr("wattpad_crawler.cli.archive_story", fake_archive_story)
    rc = main(["--output", str(output_dir), "url", "https://www.wattpad.com/1529869290"])

    assert rc == 0
    assert captured["sid"] == "123456789"


@pytest.mark.parametrize("cmd_args", [
    ["story", "12345"],
    ["url", "https://www.wattpad.com/story/12345-foo"],
])
def test_main_direct_story_commands_do_not_run_auth_probe(output_dir, monkeypatch, cmd_args):
    """Direct story archival should let the real story fetch decide auth."""
    (output_dir / "_config.toml").write_text(
        'cookie = "valid-looking-cookie"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "wattpad_crawler.cli.validate_cookie",
        lambda client: (_ for _ in ()).throw(AuthError("probe rejected")),
    )
    captured = {}

    def fake_archive_story(cfg, client, manifest, sid, deps=None):
        captured["sid"] = sid

    monkeypatch.setattr("wattpad_crawler.cli.archive_story", fake_archive_story)

    rc = main(["--output", str(output_dir), *cmd_args])

    assert rc == 0
    assert captured["sid"] == "12345"


def test_main_library_calls_fetch_library_and_archive_many(output_dir, monkeypatch):
    monkeypatch.setattr("wattpad_crawler.cli.validate_cookie", lambda client: None)
    captured = {}

    def fake_fetch_library(client, username):
        captured["user"] = username
        return ["111", "222"]

    def fake_archive_many(cfg, client, manifest, ids, *, deps=None):
        captured["ids"] = ids
        return {sid: "done" for sid in ids}

    monkeypatch.setattr("wattpad_crawler.api.user.fetch_library", fake_fetch_library)
    monkeypatch.setattr("wattpad_crawler.cli.archive_many", fake_archive_many)
    rc = main(["--output", str(output_dir), "library", "--user", "alice"])
    assert rc == 0
    assert captured["user"] == "alice"
    assert captured["ids"] == ["111", "222"]


def test_main_list_calls_fetch_list_and_archive_many(output_dir, monkeypatch):
    monkeypatch.setattr("wattpad_crawler.cli.validate_cookie", lambda client: None)
    captured = {}

    def fake_fetch_list(client, lid):
        captured["lid"] = lid
        return ["1", "2"]

    def fake_archive_many(cfg, client, manifest, ids, *, deps=None):
        captured["ids"] = ids
        return {}

    monkeypatch.setattr("wattpad_crawler.api.user.fetch_list_story_ids", fake_fetch_list)
    monkeypatch.setattr("wattpad_crawler.cli.archive_many", fake_archive_many)
    rc = main(["--output", str(output_dir), "list", "L1"])
    assert rc == 0
    assert captured["lid"] == "L1"


def test_main_status_does_not_make_network_calls(output_dir, monkeypatch, capsys):
    """status reads only the local manifest; no network."""
    rc = main(["--output", str(output_dir), "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Stories:" in out
    assert "Parts:" in out


def test_main_reset_marks_story_pending(output_dir, capsys):
    from wattpad_crawler.archive.state import Manifest
    from wattpad_crawler.models import Part, Story

    m = Manifest(output_dir).connect()
    story = Story(
        story_id="123",
        title="T",
        author_username="alice",
        parts=[Part(part_id="p1", ordinal=1, title="One", url="https://w/p1")],
    )
    m.upsert_story(story)
    m.upsert_parts(story)
    m.set_story_status("123", "done")
    m.set_part_status("123", "p1", "done", body_hash="abc")
    m.close()

    rc = main(["--output", str(output_dir), "reset", "123"])

    assert rc == 0
    assert "Reset story 123" in capsys.readouterr().out
    m = Manifest(output_dir).connect()
    assert m.get_story("123")["status"] == "pending"
    assert m.get_part("123", "p1")["body_hash"] is None
    m.close()


def test_main_reset_missing_story_exits_2(output_dir, capsys):
    rc = main(["--output", str(output_dir), "reset", "404"])

    assert rc == 2
    assert "Story not found" in capsys.readouterr().err


def test_parser_serve_command():
    parser = build_parser()
    args = parser.parse_args(["serve"])
    assert args.cmd == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_parser_serve_with_custom_host_port():
    parser = build_parser()
    args = parser.parse_args(["serve", "--host", "0.0.0.0", "--port", "9000"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_main_serve_invokes_uvicorn(output_dir, monkeypatch):
    captured = {}

    def fake_run(app, host, port, log_level):
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    rc = main(["--output", str(output_dir), "serve", "--host", "127.0.0.1", "--port", "9000"])
    assert rc == 0
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9000


# ---- AUTH-02 tests (Phase 2 / Plan 03) ----

def _seed_blank_cookie_config(output_dir: Path) -> None:
    """Pre-create _config.toml with empty cookie so load_config returns cfg.cookie=''."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "_config.toml").write_text(
        'cookie = ""\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n',
        encoding="utf-8",
    )


def test_main_collection_auth_failure_exits_2(output_dir, monkeypatch, capsys):
    """Blank cookie + collection command exits 2 before collection API calls."""
    _seed_blank_cookie_config(output_dir)
    monkeypatch.setattr(
        "wattpad_crawler.api.user.fetch_library",
        lambda *a, **kw: pytest.fail("fetch_library was called despite blank cookie"),
    )
    rc = main(["--output", str(output_dir), "library", "--user", "alice"])
    captured = capsys.readouterr()
    assert rc == 2, f"Expected exit code 2, got {rc}"
    assert "AuthError" in captured.err, f"Stderr missing 'AuthError': {captured.err!r}"
    assert "/setup" in captured.err, f"Stderr missing remediation hint '/setup': {captured.err!r}"


def test_main_auth_probe_network_error_exits_2_without_traceback(
    output_dir,
    monkeypatch,
    capsys,
):
    _seed_blank_cookie_config(output_dir)
    (output_dir / "_config.toml").write_text(
        'cookie = "valid-looking-cookie"\nrate_limit_per_sec = 2.0\nworkers_per_story = 3\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "wattpad_crawler.cli.validate_cookie",
        lambda client: (_ for _ in ()).throw(httpx.ConnectError("simulated reset")),
    )
    monkeypatch.setattr(
        "wattpad_crawler.api.user.fetch_library",
        lambda *a, **kw: pytest.fail("fetch_library was called despite network error"),
    )

    rc = main(["--output", str(output_dir), "library", "--user", "alice"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "NetworkError" in captured.err
    assert "simulated reset" in captured.err
    assert "Traceback" not in captured.err


def test_main_url_resolve_error_exits_2_without_traceback(output_dir, monkeypatch, capsys):
    monkeypatch.setattr(
        "wattpad_crawler.cli.resolve_url_story_id",
        lambda client, target: (_ for _ in ()).throw(ResolveError("not a Wattpad URL")),
    )

    rc = main(["--output", str(output_dir), "url", "https://example.com/nope"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "ResolveError" in captured.err
    assert "not a Wattpad URL" in captured.err
    assert "Traceback" not in captured.err


def test_main_http_status_error_exits_2_without_traceback(output_dir, monkeypatch, capsys):
    request = httpx.Request("GET", "https://www.wattpad.com/api/v3/stories/bad")
    response = httpx.Response(400, request=request)
    error = httpx.HTTPStatusError("400 Bad Request", request=request, response=response)
    monkeypatch.setattr(
        "wattpad_crawler.cli.archive_story",
        lambda *a, **kw: (_ for _ in ()).throw(error),
    )

    rc = main(["--output", str(output_dir), "story", "12345"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "HTTPError" in captured.err
    assert "400" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("cmd_args,downstream_attr", [
    (["library", "--user", "alice"], "wattpad_crawler.cli.archive_many"),
    (["list", "L1"], "wattpad_crawler.cli.archive_many"),
])
def test_main_collection_archive_branches_gated(output_dir, monkeypatch, cmd_args, downstream_attr):
    """Collection archive branches are gated because they require auth."""
    _seed_blank_cookie_config(output_dir)
    # Force validate_cookie to raise — guarantees we go through the AuthError path
    # regardless of how the cookie short-circuit is implemented.
    monkeypatch.setattr(
        "wattpad_crawler.cli.validate_cookie",
        lambda client: (_ for _ in ()).throw(AuthError("simulated auth failure")),
    )
    # If the downstream archive function is reached, the gate failed.
    monkeypatch.setattr(
        downstream_attr,
        lambda *a, **kw: pytest.fail(f"{downstream_attr} was called despite AuthError"),
    )
    rc = main(["--output", str(output_dir), *cmd_args])
    assert rc == 2, f"Expected exit code 2 for {cmd_args}, got {rc}"


def test_main_status_skips_validation(output_dir, monkeypatch):
    """AUTH-02 / D-06: status reads local sqlite only — does not call validate_cookie."""
    _seed_blank_cookie_config(output_dir)
    monkeypatch.setattr(
        "wattpad_crawler.cli.validate_cookie",
        lambda client: pytest.fail("validate_cookie was called for `status` command"),
    )
    rc = main(["--output", str(output_dir), "status"])
    assert rc == 0


def test_main_serve_skips_validation(output_dir, monkeypatch):
    """AUTH-02 / D-06: serve does not call validate_cookie at startup
    (web /setup covers it interactively)."""
    _seed_blank_cookie_config(output_dir)
    monkeypatch.setattr(
        "wattpad_crawler.cli.validate_cookie",
        lambda client: pytest.fail("validate_cookie was called for `serve` command"),
    )
    uvicorn_called = {"n": 0}

    def fake_run(*args, **kwargs):
        uvicorn_called["n"] += 1

    monkeypatch.setattr("wattpad_crawler.cli.uvicorn.run", fake_run)
    rc = main(["--output", str(output_dir), "serve"])
    assert rc == 0
    assert uvicorn_called["n"] == 1
