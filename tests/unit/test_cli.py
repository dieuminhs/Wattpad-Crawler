from pathlib import Path

import pytest

from wattpad_crawler.cli import build_parser, main


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


def test_main_story_calls_archive_story(output_dir, monkeypatch):
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


def test_main_library_calls_fetch_library_and_archive_many(output_dir, monkeypatch):
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
