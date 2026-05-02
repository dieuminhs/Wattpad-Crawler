import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    output_dir: Path
    cookie: str = ""
    rate_limit_per_sec: float = 2.0
    workers_per_story: int = 3
    user_agent: str = "wattpad-crawler/0.1 (+local archive tool)"


_DEFAULT_TOML = (
    '# Paste your Wattpad session cookie here (the value of the "token" cookie)\n'
    'cookie = ""\n'
    "rate_limit_per_sec = 2.0\n"
    "workers_per_story = 3\n"
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
    if rate <= 0:
        raise ConfigError(f"rate_limit_per_sec must be > 0 (got {rate}) in {config_path}")
    if workers < 1:
        raise ConfigError(f"workers_per_story must be >= 1 (got {workers}) in {config_path}")
    return Config(
        output_dir=output_dir,
        cookie=data.get("cookie", ""),
        rate_limit_per_sec=rate,
        workers_per_story=workers,
    )
