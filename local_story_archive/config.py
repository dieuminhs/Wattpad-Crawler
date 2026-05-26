import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    pass


EXPORT_PRESETS = {"classic", "cozy", "compact"}


@dataclass(frozen=True)
class Config:
    output_dir: Path
    cookie: str = ""
    rate_limit_per_sec: float = 2.0
    workers_per_story: int = 3
    compact_after_archive: bool = True
    export_preset: str = "classic"
    user_agent: str = "local-story-archive/0.1 (+local archive tool)"


_DEFAULT_TOML = (
    '# Paste your Wattpad session cookie here (the value of the "token" cookie)\n'
    'cookie = ""\n'
    "rate_limit_per_sec = 2.0\n"
    "workers_per_story = 3\n"
    "compact_after_archive = true\n"
    'export_preset = "classic"\n'
)


def load_config(output_dir: Path) -> Config:
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = output_dir / "_config.toml"
    if not config_path.exists():
        config_path.write_text(_DEFAULT_TOML, encoding="utf-8")
        raw = _DEFAULT_TOML
    else:
        raw = config_path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {config_path}: {e}") from e
    try:
        rate = float(data.get("rate_limit_per_sec", 2.0))
        workers = int(data.get("workers_per_story", 3))
    except (TypeError, ValueError) as e:
        raise ConfigError(f"Invalid value type in {config_path}: {e}") from e
    compact_after_archive = data.get("compact_after_archive", True)
    if not isinstance(compact_after_archive, bool):
        raise ConfigError(
            f"compact_after_archive must be true or false in {config_path}"
        )
    export_preset = str(data.get("export_preset", "classic"))
    if export_preset not in EXPORT_PRESETS:
        allowed = ", ".join(sorted(EXPORT_PRESETS))
        raise ConfigError(
            f"export_preset must be one of {allowed} (got {export_preset!r}) in {config_path}"
        )
    if rate <= 0:
        raise ConfigError(f"rate_limit_per_sec must be > 0 (got {rate}) in {config_path}")
    if workers < 1:
        raise ConfigError(f"workers_per_story must be >= 1 (got {workers}) in {config_path}")
    return Config(
        output_dir=output_dir,
        cookie=data.get("cookie", ""),
        rate_limit_per_sec=rate,
        workers_per_story=workers,
        compact_after_archive=compact_after_archive,
        export_preset=export_preset,
    )
