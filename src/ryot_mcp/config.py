"""Settings from the environment. Anything ambiguous refuses to start."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

MIN_TOKEN = 16
DEFAULT_LISTEN = "0.0.0.0:8080"
DEFAULT_RENEW_DAYS = 30
# Ryot sessions last USERS_TOKEN_VALID_FOR_DAYS (90 by default); renewing
# later than this leaves too little room for a server that was down a while.
MAX_RENEW_DAYS = 80


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    listen_host: str
    listen_port: int
    url: str
    # The session the operator minted in Ryot (Settings › Integrations ›
    # Create API token). Optional: the saved, renewed one is preferred.
    seed_session: str | None = field(repr=False)
    state_dir: Path | None
    renew_after_days: int
    # ISO 3166 country whose streaming services get_title lists; None = every one.
    watch_region: str | None = None


def _port(name: str, value: str) -> int:
    if not value.strip().isdigit() or not 0 < int(value) < 65536:
        raise ConfigError(f"{name} must be a TCP port")
    return int(value)


def _listen(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not host:
        raise ConfigError("MCP_LISTEN must be host:port")
    return host.strip("[]"), _port("MCP_LISTEN", port)


def _url(value: str) -> str:
    value = value.strip()
    if not value:
        raise ConfigError("RYOT_URL must be set, e.g. http://ryot:8000")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError("RYOT_URL must be an http:// or https:// URL")
    if parts.username or parts.password:
        raise ConfigError("RYOT_URL must not carry credentials; use RYOT_SESSION")
    if parts.query or parts.fragment:
        raise ConfigError("RYOT_URL must not have a query or fragment")
    return value.rstrip("/")


def _session(value: str) -> str | None:
    # A token pasted with its header prefix is still the token.
    value = value.strip().removeprefix("Bearer ").strip()
    if not value:
        return None
    if any(c in value for c in " \t\r\n\0"):
        raise ConfigError("RYOT_SESSION must be a single token with no spaces")
    return value


def _state_dir(value: str) -> Path | None:
    value = value.strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ConfigError("RYOT_STATE_DIR must be an absolute path")
    if not path.is_dir():
        raise ConfigError(f"RYOT_STATE_DIR {value} is not a directory")
    if not os.access(path, os.W_OK):
        raise ConfigError(f"RYOT_STATE_DIR {value} is not writable")
    return path


def _renew_days(value: str) -> int:
    value = value.strip() or str(DEFAULT_RENEW_DAYS)
    if not value.isdigit() or not 1 <= int(value) <= MAX_RENEW_DAYS:
        raise ConfigError(f"RYOT_RENEW_AFTER_DAYS must be a whole number of days from 1 to {MAX_RENEW_DAYS}")
    return int(value)


def _region(value: str) -> str | None:
    value = value.strip().upper()
    if not value:
        return None
    if len(value) != 2 or not value.isalpha():
        raise ConfigError("RYOT_WATCH_REGION must be a two-letter country code, e.g. AR")
    return value


def load(env: Mapping[str, str] | None = None, *, serving: bool = True) -> Settings:
    """The server's settings; with serving=False, what `login` and `status` need
    (no bearer token, and a state directory is required)."""
    env = os.environ if env is None else env
    token = env.get("MCP_BEARER_TOKEN", "")
    if serving and len(token) < MIN_TOKEN:
        raise ConfigError(f"MCP_BEARER_TOKEN must be set (at least {MIN_TOKEN} characters)")
    if not serving and not env.get("RYOT_STATE_DIR", "").strip():
        raise ConfigError("RYOT_STATE_DIR must be set: login saves the session there")
    host, port = _listen(env.get("MCP_LISTEN", DEFAULT_LISTEN))
    return Settings(
        token=token,
        listen_host=host,
        listen_port=port,
        url=_url(env.get("RYOT_URL", "")),
        seed_session=_session(env.get("RYOT_SESSION", "")),
        state_dir=_state_dir(env.get("RYOT_STATE_DIR", "")),
        renew_after_days=_renew_days(env.get("RYOT_RENEW_AFTER_DAYS", "")),
        watch_region=_region(env.get("RYOT_WATCH_REGION", "")),
    )
