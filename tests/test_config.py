from __future__ import annotations

from pathlib import Path

import pytest

from ryot_mcp import config

OK = {"MCP_BEARER_TOKEN": "x" * 16, "RYOT_URL": "http://ryot:8000/"}


def load(**extra: str) -> config.Settings:
    return config.load({**OK, **extra})


def test_minimal() -> None:
    s = load()
    assert (s.url, s.listen_host, s.listen_port) == ("http://ryot:8000", "0.0.0.0", 8080)
    assert s.seed_session is None and s.state_dir is None and s.renew_after_days == 30


def test_secrets_stay_out_of_repr(tmp_path: Path) -> None:
    s = load(RYOT_SESSION="Bearer abc123", RYOT_STATE_DIR=str(tmp_path))
    assert s.seed_session == "abc123"
    assert s.state_dir == tmp_path
    assert "abc123" not in repr(s) and "x" * 16 not in repr(s)


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ({"MCP_BEARER_TOKEN": "short"}, "MCP_BEARER_TOKEN"),
        ({"RYOT_URL": ""}, "RYOT_URL must be set"),
        ({"RYOT_URL": "ftp://ryot"}, "http://"),
        ({"RYOT_URL": "http://u:p@ryot:8000"}, "credentials"),
        ({"RYOT_URL": "http://ryot:8000/?a=1"}, "query"),
        ({"MCP_LISTEN": "8080"}, "host:port"),
        ({"MCP_LISTEN": "0.0.0.0:99999"}, "TCP port"),
        ({"RYOT_SESSION": "two words"}, "single token"),
        ({"RYOT_STATE_DIR": "relative/dir"}, "absolute"),
        ({"RYOT_STATE_DIR": "/nonexistent/ryot-mcp"}, "not a directory"),
        ({"RYOT_RENEW_AFTER_DAYS": "0"}, "RYOT_RENEW_AFTER_DAYS"),
        ({"RYOT_RENEW_AFTER_DAYS": "81"}, "RYOT_RENEW_AFTER_DAYS"),
        ({"RYOT_RENEW_AFTER_DAYS": "ten"}, "RYOT_RENEW_AFTER_DAYS"),
    ],
)
def test_refuses(extra: dict[str, str], needle: str) -> None:
    with pytest.raises(config.ConfigError, match=needle):
        config.load({**OK, **extra})
