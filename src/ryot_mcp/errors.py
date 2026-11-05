"""Typed errors. Every failure a tool reports has a `kind` the model can act on."""

from __future__ import annotations

from typing import Any


class RyotError(Exception):
    """Base of every error a tool returns to the model."""

    kind = "error"

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.kind, "message": self.message}
        out.update(self.detail)
        return out


class Unavailable(RyotError):
    """Ryot refused the connection, timed out, or answered 5xx."""

    kind = "ryot_unavailable"


class AuthFailed(RyotError):
    """No Ryot session works: none was given, or every one has expired."""

    kind = "auth_failed"


class NotFound(RyotError):
    kind = "not_found"


class InvalidArgument(RyotError):
    kind = "invalid_argument"


class UpstreamError(RyotError):
    """Ryot answered with an error of its own, or something this server does not understand."""

    kind = "upstream_error"


class WouldDownload(RyotError):
    """The collection is synced to Radarr or Sonarr, and download=true was not given."""

    kind = "would_download"
