"""The MCP layer end to end: a real HTTP server, the SDK's own client, the fake Ryot."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import anyio
import httpx
import pytest
import uvicorn
from conftest import TOKEN, FakeRyot, movie, settings
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from ryot_mcp.client import Ryot
from ryot_mcp.server import INSTRUCTIONS, build_app
from ryot_mcp.service import Tracker

EXPECTED = {
    # name: (readOnly, destructive, idempotent)
    "search": (True, False, True),
    "get_title": (True, False, True),
    "similar": (True, False, True),
    "trending": (True, False, True),
    "my_media": (True, False, True),
    "list_collections": (True, False, True),
    "get_collection": (True, False, True),
    "upcoming": (True, False, True),
    "log_seen": (False, False, False),
    "start": (False, False, False),
    "update_progress": (False, False, True),
    "remove_seen": (False, True, True),
    "review": (False, False, False),
    "delete_review": (False, True, True),
    "add_to_collection": (False, False, True),
    "remove_from_collection": (False, False, True),
    "create_collection": (False, False, True),
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Running:
    def __init__(self, port: int, fake: FakeRyot) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.fake = fake


@pytest.fixture()
def server(fake: FakeRyot) -> Iterator[Running]:
    ryot = Ryot(settings(), transport=httpx.MockTransport(fake.handler))
    port = free_port()
    config = uvicorn.Config(build_app(TOKEN, ryot, Tracker(ryot)), host="127.0.0.1", port=port, log_level="error", lifespan="on")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(250):
        if srv.started:
            break
        time.sleep(0.02)
    yield Running(port, fake)
    srv.should_exit = True
    t.join(5)


def call(base: str, fn: Any) -> Any:
    async def go() -> Any:
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30) as http:
            async with streamable_http_client(f"{base}/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    return await fn(session, init)

    return anyio.run(go)


def test_tools_and_annotations(server: Running) -> None:
    async def fn(session: ClientSession, init: types.InitializeResult) -> Any:
        return init, await session.list_tools()

    init, listed = call(server.base, fn)
    assert init.serverInfo.name == "ryot-mcp"
    assert init.instructions == INSTRUCTIONS
    assert "untrusted" in INSTRUCTIONS
    got = {t.name: t for t in listed.tools}
    assert set(got) == set(EXPECTED)
    for name, (ro, destructive, idem) in EXPECTED.items():
        a = got[name].annotations
        assert (a.readOnlyHint, a.destructiveHint, a.idempotentHint, a.openWorldHint) == (ro, destructive, idem, False), name
        assert got[name].inputSchema["additionalProperties"] is False


def test_bearer_auth_and_health(server: Running) -> None:
    with httpx.Client() as http:
        assert http.post(f"{server.base}/mcp", json={}).status_code == 401
        assert http.post(f"{server.base}/mcp", json={}, headers={"Authorization": "Bearer wrong-token-000000"}).status_code == 401
        r = http.get(f"{server.base}/healthz")
        assert (r.status_code, r.text) == (200, "ok")
        server.fake.sessions.clear()
        r = http.get(f"{server.base}/healthz")
        assert (r.status_code, r.text) == (503, "unavailable: auth_failed")
        server.fake.down = True
        r = http.get(f"{server.base}/healthz")
        assert (r.status_code, r.text) == (503, "unavailable: ryot_unavailable")


def test_a_call_round_trip(server: Running) -> None:
    server.fake.add(movie("met_1", "Spirited Away"))

    async def fn(session: ClientSession, _: Any) -> types.CallToolResult:
        return await session.call_tool("get_title", {"id": "met_1"})

    result = call(server.base, fn)
    assert not result.isError
    assert result.structuredContent == json.loads(result.content[0].text)
    assert result.structuredContent["title"] == "Spirited Away"


def test_errors_are_typed(server: Running) -> None:
    async def fn(session: ClientSession, _: Any) -> tuple[types.CallToolResult, types.CallToolResult]:
        missing = await session.call_tool("get_title", {"id": "met_missing"})
        bad = await session.call_tool("log_seen", {"id": "met_1", "unexpected": 1})
        return missing, bad

    missing, bad = call(server.base, fn)
    assert missing.isError and missing.structuredContent["error"] == "not_found"
    assert bad.isError
