from pathlib import Path

import pytest

from wattpad_crawler.config import Config, ConfigError, load_config


def test_load_config_creates_default_when_missing(output_dir: Path):
    cfg = load_config(output_dir)
    assert isinstance(cfg, Config)
    assert cfg.output_dir == output_dir
    assert cfg.cookie == ""
    assert cfg.rate_limit_per_sec == 2.0
    assert cfg.workers_per_story == 3
    assert (output_dir / "_config.toml").exists()


def test_load_config_reads_existing(output_dir: Path):
    (output_dir / "_config.toml").write_text(
        'cookie = "abc123"\n'
        "rate_limit_per_sec = 0.5\n"
        "workers_per_story = 5\n"
    )
    cfg = load_config(output_dir)
    assert cfg.cookie == "abc123"
    assert cfg.rate_limit_per_sec == 0.5
    assert cfg.workers_per_story == 5


def test_load_config_rejects_bad_toml(output_dir: Path):
    (output_dir / "_config.toml").write_text("not a [valid toml")
    with pytest.raises(ConfigError):
        load_config(output_dir)
