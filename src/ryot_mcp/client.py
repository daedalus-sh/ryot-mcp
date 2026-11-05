"""Ryot's GraphQL API, and the session this server holds with it.

Ryot authenticates a request with `Authorization: Bearer <session>`. A
session lasts USERS_TOKEN_VALID_FOR_DAYS (90 by default) from when it was
minted, and using it does not extend it. So the server keeps its own: it
starts from the session the operator minted (RYOT_SESSION, from Ryot ›
Settings › Integrations › Generate auth token), mints a fresh one with
`generateAuthToken` once the one it holds is RYOT_RENEW_AFTER_DAYS old, and
saves it in RYOT_STATE_DIR so a restart picks up where it left off. A saved
session is preferred over the seed, because the seed only ages.

Without a state directory the renewal still happens, in memory only, and a
restart falls back to the seed."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from . import queries
from .config import Settings
from .errors import AuthFailed, NotFound, RyotError, Unavailable, UpstreamError

log = logging.getLogger("ryot_mcp")

STATE_FILE = "session.json"
TIMEOUT = httpx.Timeout(30.0, connect=5.0)
# What Ryot answers when a request's session is missing, unknown or expired.
AUTH_ERRORS = frozenset({"NO_USER_ID", "NO_SESSION_ID", "SESSION_EXPIRED", "SESSION_INVALID"})
NO_SESSION = (
    "Ryot accepted no session. Mint one in Ryot (Settings › Integrations › Generate auth token), "
    "give it to this server as RYOT_SESSION and restart it."
)


@dataclass(frozen=True)
class Me:
    id: str
    name: str
    review_scale: str


@dataclass
class _Held:
    token: str
    # When it was minted, if this server minted it; None for a seed.
    minted_at: datetime | None


def _now() -> datetime:
    return datetime.now(UTC)


class Ryot:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Any = _now,
    ) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(base_url=settings.url, timeout=TIMEOUT, transport=transport)
        self._clock = clock
        self._renew_after = timedelta(days=settings.renew_after_days)
        self._held: _Held | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── transport ──────────────────────────────────────────────────────

    async def _post(self, document: str, variables: dict[str, Any] | None, token: str | None) -> dict[str, Any]:
        headers = {"content-type": "application/json"}
        if token is not None:
            headers["authorization"] = f"Bearer {token}"
        body = {"query": document, "variables": variables or {}}
        try:
            resp = await self._http.post("/backend/graphql", content=json.dumps(body), headers=headers)
        except httpx.TimeoutException:
            raise Unavailable("Ryot did not answer in time") from None
        except httpx.TransportError as exc:
            raise Unavailable(f"Ryot could not be reached: {type(exc).__name__}") from None
        if resp.status_code >= 500:
            raise Unavailable(f"Ryot answered HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise UpstreamError(f"Ryot answered HTTP {resp.status_code}")
        try:
            doc = resp.json()
        except ValueError:
            raise UpstreamError("Ryot answered something that is not JSON") from None
        if not isinstance(doc, dict):
            raise UpstreamError("Ryot answered an unexpected document")
        return doc

    @staticmethod
    def _first_error(doc: dict[str, Any]) -> str | None:
        errors = doc.get("errors")
        if not errors:
            return None
        first = errors[0] if isinstance(errors, list) and errors else {}
        message = first.get("message") if isinstance(first, dict) else None
        return str(message or "Ryot returned an error")[:300]

    # ── the session ────────────────────────────────────────────────────

    async def _me(self, token: str) -> Me | None:
        """Who a session belongs to, or None if Ryot does not accept it."""
        doc = await self._post(queries.USER_DETAILS, None, token)
        error = self._first_error(doc)
        if error is not None:
            if error in AUTH_ERRORS:
                return None
            raise UpstreamError(error)
        details = (doc.get("data") or {}).get("userDetails") or {}
        if details.get("__typename") != "UserDetails":
            return None
        scale = ((details.get("preferences") or {}).get("general") or {}).get("reviewScale") or "OUT_OF_HUNDRED"
        return Me(id=details["id"], name=details["name"], review_scale=scale)

    def _state_path(self) -> Path | None:
        return self.settings.state_dir / STATE_FILE if self.settings.state_dir else None

    def _load_saved(self) -> _Held | None:
        path = self._state_path()
        if path is None or not path.exists():
            return None
        try:
            doc = json.loads(path.read_text())
            return _Held(token=str(doc["token"]), minted_at=datetime.fromisoformat(doc["minted_at"]))
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("ignoring an unreadable %s", path)
            return None

    def _save(self, held: _Held) -> None:
        path = self._state_path()
        if path is None or held.minted_at is None:
            return
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".session.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"token": held.token, "minted_at": held.minted_at.isoformat()}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    async def _mint(self, current: str) -> _Held:
        doc = await self._post(queries.GENERATE_AUTH_TOKEN, None, current)
        error = self._first_error(doc)
        if error is not None:
            raise AuthFailed(NO_SESSION) if error in AUTH_ERRORS else UpstreamError(error)
        token = (doc.get("data") or {}).get("generateAuthToken")
        if not isinstance(token, str) or not token:
            raise UpstreamError("Ryot minted no session")
        held = _Held(token=token, minted_at=self._clock())
        if await self._me(token) is None:
            raise UpstreamError("the session Ryot just minted is not accepted")
        self._save(held)
        log.info("renewed the Ryot session")
        return held

    async def _establish(self) -> None:
        candidates: list[_Held] = []
        saved = self._load_saved()
        if saved is not None:
            candidates.append(saved)
        if self.settings.seed_session:
            candidates.append(_Held(token=self.settings.seed_session, minted_at=None))
        for held in candidates:
            if await self._me(held.token) is None:
                continue
            # A seed's age is unknown: replace it at once with one whose age is.
            self._held = await self._mint(held.token) if held.minted_at is None else held
            return
        self._held = None
        raise AuthFailed(NO_SESSION)

    async def session(self) -> str:
        async with self._lock:
            if self._held is None:
                await self._establish()
            assert self._held is not None
            if self._held.minted_at is not None and self._clock() - self._held.minted_at >= self._renew_after:
                self._held = await self._mint(self._held.token)
            return self._held.token

    async def renew_if_due(self) -> None:
        """For the background loop: renew if due, never raise."""
        try:
            await self.session()
        except RyotError as err:
            log.warning("session check failed: %s", err.message)

    # ── calls ──────────────────────────────────────────────────────────

    async def call(self, document: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run an operation as this server's user and return its `data`.

        A session Ryot stops accepting mid-life (expired early, revoked) is
        dropped and the next best one tried, once."""
        for attempt in (1, 2):
            token = await self.session()
            doc = await self._post(document, variables, token)
            error = self._first_error(doc)
            if error is None:
                data = doc.get("data")
                if not isinstance(data, dict):
                    raise UpstreamError("Ryot answered without data")
                return data
            if error in AUTH_ERRORS and attempt == 1:
                async with self._lock:
                    if self._held is not None and self._held.token == token:
                        self._held = None
                continue
            if error in AUTH_ERRORS:
                raise AuthFailed(NO_SESSION)
            if "not found" in error.lower() or "does not exist" in error.lower():
                raise NotFound(error)
            raise UpstreamError(error)
        raise AuthFailed(NO_SESSION)  # pragma: no cover - the loop always returns or raises

    async def me(self) -> Me:
        token = await self.session()
        me = await self._me(token)
        if me is None:
            raise AuthFailed(NO_SESSION)
        return me

    async def public(self, document: str) -> dict[str, Any]:
        """An operation that needs no session (coreDetails)."""
        doc = await self._post(document, None, None)
        error = self._first_error(doc)
        if error is not None:
            raise UpstreamError(error)
        return doc.get("data") or {}
