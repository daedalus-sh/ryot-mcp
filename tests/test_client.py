"""The session: which one is used, when it is renewed, where it is kept."""

from __future__ import annotations

import asyncio
import json
import stat
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import SEED, Clock, FakeRyot, make

from ryot_mcp import queries
from ryot_mcp.errors import AuthFailed, Unavailable


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def test_a_seed_is_replaced_at_once_and_saved(fake: FakeRyot, clock: Clock, tmp_path: Path) -> None:
    ryot, _ = make(fake, clock, state_dir=tmp_path)
    assert run(ryot.session()) == "minted-1"
    saved = tmp_path / "session.json"
    doc = json.loads(saved.read_text())
    assert doc["token"] == "minted-1"
    assert doc["minted_at"] == clock.now.isoformat()
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    # The seed's age is unknown, so it was used once, to mint.
    assert [t for op, _, t in fake.calls if op == "GenerateAuthToken"] == [SEED]


def test_a_saved_session_is_preferred_and_not_renewed_while_fresh(fake: FakeRyot, clock: Clock, tmp_path: Path) -> None:
    fake.sessions.add("saved-1")
    (tmp_path / "session.json").write_text(json.dumps({"token": "saved-1", "minted_at": clock.now.isoformat()}))
    clock.now += timedelta(days=29)
    ryot, _ = make(fake, clock, state_dir=tmp_path)
    assert run(ryot.session()) == "saved-1"
    assert fake.minted == 0


def test_renewed_once_due(fake: FakeRyot, clock: Clock, tmp_path: Path) -> None:
    ryot, _ = make(fake, clock, state_dir=tmp_path)
    assert run(ryot.session()) == "minted-1"
    clock.now += timedelta(days=30)
    assert run(ryot.session()) == "minted-2"
    assert json.loads((tmp_path / "session.json").read_text())["token"] == "minted-2"


def test_an_expired_saved_session_falls_back_to_the_seed(fake: FakeRyot, clock: Clock, tmp_path: Path) -> None:
    (tmp_path / "session.json").write_text(json.dumps({"token": "gone", "minted_at": clock.now.isoformat()}))
    ryot, _ = make(fake, clock, state_dir=tmp_path)
    assert run(ryot.session()) == "minted-1"


def test_an_unreadable_state_file_is_ignored(fake: FakeRyot, clock: Clock, tmp_path: Path) -> None:
    (tmp_path / "session.json").write_text("{not json")
    ryot, _ = make(fake, clock, state_dir=tmp_path)
    assert run(ryot.session()) == "minted-1"


def test_no_session_at_all(fake: FakeRyot) -> None:
    ryot, _ = make(fake, seed=None)
    with pytest.raises(AuthFailed, match="Generate auth token"):
        run(ryot.session())


def test_a_rejected_seed(fake: FakeRyot) -> None:
    fake.sessions.clear()
    ryot, _ = make(fake)
    with pytest.raises(AuthFailed):
        run(ryot.session())


def test_without_a_state_dir_renewal_stays_in_memory(fake: FakeRyot, clock: Clock) -> None:
    ryot, _ = make(fake, clock)
    assert run(ryot.session()) == "minted-1"


def test_a_session_revoked_mid_life_is_replaced_once(fake: FakeRyot, clock: Clock, tmp_path: Path) -> None:
    ryot, _ = make(fake, clock, state_dir=tmp_path)

    async def go() -> dict:
        await ryot.session()
        fake.sessions.discard("minted-1")
        return await ryot.call(queries.TRENDING)

    assert run(go()) == {"trendingMetadata": []}
    # The saved session failed, so the seed minted a new one.
    assert fake.minted == 2


def test_renew_if_due_never_raises(fake: FakeRyot) -> None:
    fake.down = True
    ryot, _ = make(fake)
    run(ryot.renew_if_due())


def test_unreachable(fake: FakeRyot) -> None:
    fake.down = True
    ryot, _ = make(fake)
    with pytest.raises(Unavailable):
        run(ryot.session())
