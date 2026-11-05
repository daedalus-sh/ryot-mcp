"""ryot-mcp: run the server."""

from __future__ import annotations

import logging
import sys

import uvicorn

from . import config
from .client import Ryot
from .server import build_app
from .service import Tracker


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    # One line per tool call is ours; the libraries' per-request lines (with
    # URLs and search terms) stay out of the log.
    for noisy in ("httpx", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        settings = config.load()
    except config.ConfigError as exc:
        print(f"ryot-mcp: {exc}", file=sys.stderr)
        sys.exit(2)
    ryot = Ryot(settings)
    logging.getLogger("ryot_mcp").info(
        "Ryot at %s; seed session %s; state %s",
        settings.url,
        "given" if settings.seed_session else "absent",
        settings.state_dir or "in memory only",
    )
    app = build_app(settings.token, ryot, Tracker(ryot))
    uvicorn.run(app, host=settings.listen_host, port=settings.listen_port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
