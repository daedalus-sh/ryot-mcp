"""ryot-mcp: run the server, or sign it in.

    ryot-mcp           serve MCP on MCP_LISTEN
    ryot-mcp login     read a token minted in Ryot (Settings › Integrations ›
                       Create API token) from the terminal, without echo,
                       or from stdin; replace it with a session this server
                       mints, and save that in RYOT_STATE_DIR
    ryot-mcp status    whether a saved session exists, how old it is, and
                       whether Ryot accepts it (never the session itself)
    ryot-mcp apply     converge collections, integrations and email
                       notifications from a JSON spec on stdin (apply.py),
                       as the signed-in user; print what it did

A running server picks up a session `login` saved on its next call: no
restart."""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import sys

import uvicorn

from . import config
from .apply import apply
from .client import Ryot
from .errors import RyotError
from .server import build_app
from .service import Tracker

PROMPT = "Ryot token (Settings › Integrations › Create API token): "


def _read_token() -> str:
    if sys.stdin.isatty():
        return getpass.getpass(PROMPT)
    return sys.stdin.readline()


async def _login(settings: config.Settings) -> int:
    ryot = Ryot(settings)
    try:
        me = await ryot.adopt(_read_token())
    except RyotError as err:
        print(f"ryot-mcp: {err.message}", file=sys.stderr)
        return 1
    finally:
        await ryot.aclose()
    print(
        f"Signed in to Ryot as {me.name}. The server now holds its own session, renewed every {settings.renew_after_days} days."
    )
    return 0


async def _status(settings: config.Settings) -> int:
    ryot = Ryot(settings)
    try:
        status = await ryot.saved_status()
    except RyotError as err:
        print(f"ryot-mcp: {err.message}", file=sys.stderr)
        return 1
    finally:
        await ryot.aclose()
    print(json.dumps(status, indent=2))
    return 0 if status.get("accepted") else 1


async def _apply(settings: config.Settings) -> int:
    try:
        spec = json.loads(sys.stdin.read())
    except ValueError as exc:
        print(f"ryot-mcp: the spec on stdin is not JSON: {exc}", file=sys.stderr)
        return 2
    ryot = Ryot(settings)
    try:
        report = await apply(ryot, spec)
    except RyotError as err:
        print(f"ryot-mcp: {err.message}", file=sys.stderr)
        return 1
    finally:
        await ryot.aclose()
    print(json.dumps(report))
    return 0


def _serve(settings: config.Settings) -> None:
    ryot = Ryot(settings)
    logging.getLogger("ryot_mcp").info(
        "Ryot at %s; seed session %s; state %s",
        settings.url,
        "given" if settings.seed_session else "absent",
        settings.state_dir or "in memory only",
    )
    app = build_app(settings.token, ryot, Tracker(ryot, watch_region=settings.watch_region))
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port, access_log=False, log_level="warning")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    # One line per tool call is ours; the libraries' per-request lines (with
    # URLs and search terms) stay out of the log.
    for noisy in ("httpx", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if command not in ("serve", "login", "status", "apply") or len(sys.argv) > 2:
        print("usage: ryot-mcp [serve | login | status | apply]", file=sys.stderr)
        sys.exit(2)
    try:
        settings = config.load(serving=command == "serve")
    except config.ConfigError as exc:
        print(f"ryot-mcp: {exc}", file=sys.stderr)
        sys.exit(2)
    if command == "login":
        sys.exit(asyncio.run(_login(settings)))
    if command == "status":
        sys.exit(asyncio.run(_status(settings)))
    if command == "apply":
        sys.exit(asyncio.run(_apply(settings)))
    _serve(settings)


if __name__ == "__main__":
    main()
