import tomllib
from dataclasses import dataclass
from pathlib import Path

from local_story_archive.cookie_crypto import CookieCryptoError, decrypt_cookie


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
    archive_comments: bool = False
    export_preset: str = "classic"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) "
        "Gecko/20100101 Firefox/151.0"
    )


_DEFAULT_TOML = (
    '# Wattpad session cookie. Saved through the web UI as encrypted cookie_encrypted.\n'
    'cookie = ""\n'
    'cookie_encrypted = ""\n'
    "rate_limit_per_sec = 2.0\n"
    "workers_per_story = 3\n"
    "compact_after_archive = true\n"
    "archive_comments = false\n"
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
    archive_comments = data.get("archive_comments", False)
    if not isinstance(archive_comments, bool):
        raise ConfigError(f"archive_comments must be true or false in {config_path}")
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
    encrypted_cookie = str(data.get("cookie_encrypted", ""))
    if encrypted_cookie:
        try:
            cookie = decrypt_cookie(encrypted_cookie)
        except CookieCryptoError as e:
            raise ConfigError(f"Could not decrypt cookie in {config_path}: {e}") from e
    else:
        cookie = str(data.get("cookie", ""))
    return Config(
        output_dir=output_dir,
        cookie=cookie,
        rate_limit_per_sec=rate,
        workers_per_story=workers,
        compact_after_archive=compact_after_archive,
        archive_comments=archive_comments,
        export_preset=export_preset,
    )
