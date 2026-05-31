from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
from pathlib import Path


KEY_SIZE = 32
NONCE_SIZE = 16
MAC_SIZE = 32
TOKEN_PREFIX = "lsa1:"


class CookieCryptoError(Exception):
    pass


def _app_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "Local Story Archive"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Local Story Archive"
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "local-story-archive"
    return Path.home() / ".local" / "share" / "local-story-archive"


def _key_path() -> Path:
    override = os.environ.get("LOCAL_STORY_ARCHIVE_COOKIE_KEY")
    if override:
        return Path(override).expanduser()
    return _app_data_dir() / "cookie.key"


def _load_key() -> bytes:
    path = _key_path()
    if path.exists():
        key = path.read_bytes()
        if len(key) != KEY_SIZE:
            raise CookieCryptoError(f"cookie encryption key has invalid length: {path}")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(KEY_SIZE)
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = []
    counter = 0
    while sum(len(block) for block in blocks) < length:
        counter_bytes = counter.to_bytes(8, "big")
        blocks.append(hmac.new(key, nonce + counter_bytes, hashlib.sha256).digest())
        counter += 1
    return b"".join(blocks)[:length]


def encrypt_cookie(cookie: str) -> str:
    if not cookie:
        return ""
    key = _load_key()
    nonce = os.urandom(NONCE_SIZE)
    plaintext = cookie.encode("utf-8")
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = bytes(left ^ right for left, right in zip(plaintext, stream, strict=True))
    mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    payload = base64.urlsafe_b64encode(nonce + ciphertext + mac).decode("ascii")
    return TOKEN_PREFIX + payload


def decrypt_cookie(token: str) -> str:
    if not token:
        return ""
    if not token.startswith(TOKEN_PREFIX):
        raise CookieCryptoError("unsupported encrypted cookie format")
    try:
        payload = base64.urlsafe_b64decode(token[len(TOKEN_PREFIX) :].encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CookieCryptoError("encrypted cookie is not valid base64") from exc
    if len(payload) < NONCE_SIZE + MAC_SIZE:
        raise CookieCryptoError("encrypted cookie payload is too short")
    nonce = payload[:NONCE_SIZE]
    ciphertext = payload[NONCE_SIZE:-MAC_SIZE]
    mac = payload[-MAC_SIZE:]
    key = _load_key()
    expected_mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise CookieCryptoError("encrypted cookie failed integrity check")
    stream = _keystream(key, nonce, len(ciphertext))
    plaintext = bytes(left ^ right for left, right in zip(ciphertext, stream, strict=True))
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CookieCryptoError("encrypted cookie is not valid UTF-8") from exc
