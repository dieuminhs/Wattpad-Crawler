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
    assert cfg.compact_after_archive is True
    assert cfg.export_preset == "classic"
    assert (output_dir / "_config.toml").exists()


def test_load_config_reads_existing(output_dir: Path):
    (output_dir / "_config.toml").write_text(
        'cookie = "abc123"\n'
        "rate_limit_per_sec = 0.5\n"
        "workers_per_story = 5\n"
        "compact_after_archive = false\n"
        'export_preset = "cozy"\n'
    )
    cfg = load_config(output_dir)
    assert cfg.cookie == "abc123"
    assert cfg.rate_limit_per_sec == 0.5
    assert cfg.workers_per_story == 5
    assert cfg.compact_after_archive is False
    assert cfg.export_preset == "cozy"

def test_load_config_defaults_compact_after_archive_when_missing(output_dir: Path):
    (output_dir / "_config.toml").write_text('cookie = "abc123"\n')

    cfg = load_config(output_dir)

    assert cfg.compact_after_archive is True


def test_load_config_rejects_bad_toml(output_dir: Path):
    (output_dir / "_config.toml").write_text("not a [valid toml")
    with pytest.raises(ConfigError):
        load_config(output_dir)


def test_load_config_rejects_invalid_value_type(output_dir: Path):
    (output_dir / "_config.toml").write_text('workers_per_story = "lots"\n')
    with pytest.raises(ConfigError):
        load_config(output_dir)


def test_load_config_rejects_non_positive_rate_limit(output_dir: Path):
    (output_dir / "_config.toml").write_text("rate_limit_per_sec = 0\n")
    with pytest.raises(ConfigError):
        load_config(output_dir)


def test_load_config_rejects_zero_workers(output_dir: Path):
    (output_dir / "_config.toml").write_text("workers_per_story = 0\n")
    with pytest.raises(ConfigError):
        load_config(output_dir)

def test_load_config_rejects_invalid_compact_flag(output_dir: Path):
    (output_dir / "_config.toml").write_text('compact_after_archive = "yes"\n')

    with pytest.raises(ConfigError):
        load_config(output_dir)

def test_load_config_rejects_invalid_export_preset(output_dir: Path):
    (output_dir / "_config.toml").write_text('export_preset = "fancy"\n')

    with pytest.raises(ConfigError):
        load_config(output_dir)


def test_config_is_frozen():
    import dataclasses
    from pathlib import Path as _P

    from wattpad_crawler.config import Config

    cfg = Config(output_dir=_P("/tmp"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.cookie = "mutated"
