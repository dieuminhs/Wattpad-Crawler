import pytest

from wattpad_crawler.cli import build_parser


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
    from pathlib import Path
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.output == Path("./wattpad-archive")


def test_parser_custom_output_dir():
    from pathlib import Path
    parser = build_parser()
    args = parser.parse_args(["--output", "/tmp/x", "status"])
    assert args.output == Path("/tmp/x")


def test_parser_requires_subcommand():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
