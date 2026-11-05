"""`apply`: converging collections, integrations and email notifications."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from conftest import FakeRyot, make

from ryot_mcp.apply import apply
from ryot_mcp.errors import InvalidArgument

KEY = "radarr-key-not-in-any-report"


def spec() -> dict[str, Any]:
    return {
        "collections": [{"name": "Download", "description": "Sent to Radarr and Sonarr."}],
        "integrations": [
            {
                "provider": "radarr",
                "name": "Radarr",
                "base_url": "http://host.containers.internal:7878/",
                "api_key": KEY,
                "profile_id": 9,
                "root_folder": "/data/media/movies",
                "tag_ids": [],
                "collections": ["Download"],
            },
            {"provider": "jellyfin_sink", "name": "Jellyfin", "username": "santi", "metadata_provider": "tmdb"},
        ],
        "email_notifications": ["someone@example.com"],
    }


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def test_first_apply_creates_everything(fake: FakeRyot) -> None:
    ryot, _ = make(fake)
    report = run(apply(ryot, spec()))
    assert report["collections"] == {"Download": "created"}
    assert report["integrations"]["Radarr"] == {"provider": "radarr", "state": "created", "id": "int_1"}
    assert report["integrations"]["Jellyfin"] == {
        "provider": "jellyfin_sink",
        "state": "created",
        "id": "int_2",
        "webhook_path": "/_i/int_2",
    }
    assert report["email_notifications"] == {"someone@example.com": "created"}
    radarr = fake.integrations[0]
    assert radarr["provider"] == "RADARR"
    assert radarr["extraSettings"] == {"disableOnContinuousErrors": False}
    download = next(c["id"] for c in fake.collections if c["name"] == "Download")
    assert radarr["providerSpecifics"] == {
        "radarrBaseUrl": "http://host.containers.internal:7878",
        "radarrApiKey": KEY,
        "radarrProfileId": 9,
        "radarrRootFolderPath": "/data/media/movies",
        "radarrTagIds": [],
        "radarrSyncCollectionIds": [download],
    }
    assert fake.integrations[1]["providerSpecifics"] == {"jellyfinSinkUsername": "santi", "jellyfinSinkMetadataProvider": "tmdb"}
    assert KEY not in json.dumps(report)


def test_a_second_apply_updates_in_place(fake: FakeRyot) -> None:
    ryot, _ = make(fake)
    run(apply(ryot, spec()))
    changed = spec()
    changed["integrations"][0]["profile_id"] = 4
    report = run(apply(ryot, changed))
    assert report["collections"] == {"Download": "present"}
    assert report["integrations"]["Radarr"]["state"] == "updated"
    assert report["email_notifications"] == {"someone@example.com": "present"}
    assert len(fake.integrations) == 2 and len(fake.platforms) == 1
    assert fake.integrations[0]["providerSpecifics"]["radarrProfileId"] == 4
    assert len([c for c in fake.collections if c["name"] == "Download"]) == 1


def test_a_push_must_name_collections_that_exist(fake: FakeRyot) -> None:
    ryot, _ = make(fake)
    bad = spec()
    bad["collections"] = []
    with pytest.raises(InvalidArgument, match="do not exist: Download"):
        run(apply(ryot, bad))


@pytest.mark.parametrize(
    ("entry", "needle"),
    [
        ({"provider": "plex_yank", "name": "Plex"}, "provider must be one of"),
        ({"provider": "radarr"}, "needs a name"),
        ({"provider": "sonarr", "name": "Sonarr", "base_url": "http://x"}, "needs api_key, profile_id, root_folder"),
    ],
)
def test_bad_entries_are_refused(fake: FakeRyot, entry: dict[str, Any], needle: str) -> None:
    ryot, _ = make(fake)
    with pytest.raises(InvalidArgument, match=needle):
        run(apply(ryot, {"integrations": [entry]}))
