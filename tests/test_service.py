"""Each tool against the fake Ryot: what it sends, and what it answers."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import FakeRyot, empty_mine, make, movie, show

from ryot_mcp.errors import InvalidArgument, NotFound, UpstreamError, WouldDownload
from ryot_mcp.service import rating_in, when


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def seen(**extra: Any) -> dict[str, Any]:
    doc = {
        "id": "see_1",
        "state": "COMPLETED",
        "progress": "100",
        "startedOn": None,
        "finishedOn": "2026-10-01T12:00:00+00:00",
        "lastUpdatedOn": "2026-10-01T12:00:00+00:00",
        "providersConsumedOn": ["Netflix"],
        "showExtraInformation": None,
        "animeExtraInformation": None,
        "mangaExtraInformation": None,
    }
    doc.update(extra)
    return doc


# ── pure helpers ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("scale", "rating", "sent"),
    [("OUT_OF_TEN", 8.5, "8.5"), ("OUT_OF_FIVE", 8, "4"), ("OUT_OF_FIVE", 7, "3.5"), ("OUT_OF_HUNDRED", 7.5, "75")],
)
def test_rating_in(scale: str, rating: float, sent: str) -> None:
    assert rating_in(rating, scale) == sent


def test_when() -> None:
    assert when("2026-10-01", name="d") == "2026-10-01T12:00:00+00:00"
    assert when("2026-10-01T22:30:00-03:00", name="d") == "2026-10-02T01:30:00+00:00"
    with pytest.raises(InvalidArgument):
        when("yesterday", name="d")


# ── reads ─────────────────────────────────────────────────────────────────


def test_search_uses_the_kind_s_default_source_and_reports_my_state(fake: FakeRyot) -> None:
    fake.add(
        movie("met_1", "Spirited Away"),
        {
            **empty_mine(),
            "seenByUserCount": 2,
            "reviews": [{"id": "rev_1", "rating": "90", "postedBy": {"id": "usr_me"}}],
            "collections": [{"details": {"collectionName": "Owned"}}],
        },
    )
    fake.search_results = ["met_1"]
    _, t = make(fake)
    out = run(t.search({"kind": "movie", "query": "spirited"}))
    sent = fake.ops("MetadataSearch")[0]["input"]
    assert (sent["lot"], sent["source"], sent["search"]["query"]) == ("MOVIE", "TMDB", "spirited")
    assert out["source"] == "tmdb"
    assert out["results"] == [
        {
            "id": "met_1",
            "title": "Spirited Away",
            "kind": "movie",
            "year": 2001,
            "source": "tmdb",
            "provider_rating": 7.8,
            "times_seen": 2,
            "my_rating": 9.0,
            "collections": ["Owned"],
        }
    ]


def test_search_refuses_a_source_that_does_not_serve_the_kind(fake: FakeRyot) -> None:
    _, t = make(fake)
    with pytest.raises(InvalidArgument, match="anilist"):
        run(t.search({"kind": "anime", "query": "x", "source": "tmdb"}))


def test_get_title_merges_details_people_and_my_history(fake: FakeRyot) -> None:
    fake.people = {"per_1": "Hayao Miyazaki", "per_2": "Rumi Hiiragi"}
    fake.add(
        movie(
            "met_1",
            "Spirited Away",
            suggestions=["met_2", "met_3"],
            creators=[
                {"name": "Actor", "items": [{"idOrName": "per_2", "isFree": False, "character": "Chihiro"}]},
                {"name": "Director", "items": [{"idOrName": "per_1", "isFree": False, "character": None}]},
            ],
        ),
        {
            **empty_mine(),
            "seenByUserCount": 1,
            "averageRating": "90",
            "history": [seen()],
            "reviews": [
                {
                    "id": "rev_1",
                    "rating": "90",
                    "textOriginal": "Wonderful",
                    "isSpoiler": False,
                    "visibility": "PRIVATE",
                    "postedOn": "2026-10-02T00:00:00+00:00",
                    "postedBy": {"id": "usr_me"},
                    "showExtraInformation": None,
                },
                {"id": "rev_x", "rating": "10", "textOriginal": "someone else", "postedBy": {"id": "usr_other"}},
            ],
        },
    )
    _, t = make(fake)
    out = run(t.get_title({"id": "met_1"}))
    assert out["where_to_watch"] == ["Netflix"]
    assert out["runtime_minutes"] == 110
    assert out["people"] == {"Director": ["Hayao Miyazaki"], "Actor": ["Rumi Hiiragi (Chihiro)"]}
    assert out["similar_available"] == 2
    me = out["me"]
    assert me["times_seen"] == 1 and me["average_rating"] == 9.0
    assert me["history"] == [
        {
            "seen_id": "see_1",
            "state": "completed",
            "progress_percent": 100.0,
            "finished_on": "2026-10-01T12:00:00+00:00",
            "updated_on": "2026-10-01T12:00:00+00:00",
            "where": ["Netflix"],
        }
    ]
    assert [r["review_id"] for r in me["reviews"]] == ["rev_1"]
    assert me["reviews"][0]["rating"] == 9.0


def test_get_title_unknown_id(fake: FakeRyot) -> None:
    _, t = make(fake)
    with pytest.raises(NotFound):
        run(t.get_title({"id": "met_missing"}))


def test_get_collection_defaults_to_the_watchlist(fake: FakeRyot) -> None:
    fake.add(movie("met_1", "Paprika", 2006))
    fake.collection_items = {
        "col_w": [{"entityId": "met_1", "entityLot": "METADATA"}, {"entityId": "per_9", "entityLot": "PERSON"}]
    }
    _, t = make(fake)
    out = run(t.get_collection({}))
    assert out["collection"] == "Watchlist"
    assert out["items"][0]["title"] == "Paprika"
    assert out["items"][1] == {"entity_id": "per_9", "entity_type": "person"}
    assert fake.ops("CollectionContents")[0]["input"]["sort"] == {"by": "RANK", "order": "ASC"}


def test_unknown_collection_lists_the_real_ones(fake: FakeRyot) -> None:
    _, t = make(fake)
    with pytest.raises(NotFound) as err:
        run(t.get_collection({"collection": "Favourites"}))
    assert err.value.detail["available"] == ["Watchlist", "Owned"]


def test_upcoming_names_each_title_once(fake: FakeRyot) -> None:
    fake.add(show("met_s", "Severance", {1: 9, 2: 10}))
    fake.events = [
        {
            "date": "2026-11-07",
            "metadataId": "met_s",
            "showExtraInformation": {"season": 3, "episode": 1},
            "animeExtraInformation": None,
        },
        {
            "date": "2026-11-14",
            "metadataId": "met_s",
            "showExtraInformation": {"season": 3, "episode": 2},
            "animeExtraInformation": None,
        },
    ]
    _, t = make(fake)
    out = run(t.upcoming({}))
    assert out["upcoming"][1] == {
        "date": "2026-11-14",
        "id": "met_s",
        "title": "Severance",
        "kind": "show",
        "season": 3,
        "episode": 2,
    }
    assert len(fake.ops("MetadataDetails")) == 1


# ── writes ────────────────────────────────────────────────────────────────


def test_log_a_movie_watched_on_netflix(fake: FakeRyot) -> None:
    fake.add(movie("met_1", "Spirited Away"))
    _, t = make(fake)
    out = run(t.log_seen({"id": "met_1", "finished_on": "2026-11-01", "where": ["Netflix"]}))
    assert out == {"queued": True, "id": "met_1", "title": "Spirited Away", "entries": 1}
    sent = fake.ops("DeployBulkMetadataProgressUpdate")[0]["input"]
    assert sent == [
        {
            "metadataId": "met_1",
            "change": {
                "createNewCompleted": {
                    "finishedOnDate": {"timestamp": "2026-11-01T12:00:00+00:00", "providersConsumedOn": ["Netflix"]}
                }
            },
        }
    ]


def test_log_a_whole_season_and_a_whole_show(fake: FakeRyot) -> None:
    fake.add(show("met_s", "Severance", {0: 2, 1: 9, 2: 10}))
    _, t = make(fake)
    assert run(t.log_seen({"id": "met_s", "season": 2, "date_unknown": True}))["entries"] == 10
    batch = fake.ops("DeployBulkMetadataProgressUpdate")[0]["input"]
    assert batch[0]["change"] == {"createNewCompleted": {"withoutDates": {"showSeasonNumber": 2, "showEpisodeNumber": 1}}}
    # Specials (season 0) are left out unless asked for.
    assert run(t.log_seen({"id": "met_s"}))["entries"] == 19
    assert run(t.log_seen({"id": "met_s", "include_specials": True}))["entries"] == 21


def test_log_refuses_a_missing_season_and_episode_on_a_movie(fake: FakeRyot) -> None:
    fake.add(show("met_s", "Severance", {1: 9}))
    fake.add(movie("met_1", "Spirited Away"))
    _, t = make(fake)
    with pytest.raises(NotFound, match="no season 4"):
        run(t.log_seen({"id": "met_s", "season": 4}))
    with pytest.raises(InvalidArgument, match="for shows"):
        run(t.log_seen({"id": "met_1", "season": 1}))
    with pytest.raises(InvalidArgument, match="date_unknown"):
        run(t.log_seen({"id": "met_1", "date_unknown": True, "finished_on": "2026-01-01"}))


def test_started_and_finished(fake: FakeRyot) -> None:
    fake.add(movie("met_1", "Spirited Away"))
    _, t = make(fake)
    run(t.log_seen({"id": "met_1", "started_on": "2026-10-30", "finished_on": "2026-11-01"}))
    change = fake.ops("DeployBulkMetadataProgressUpdate")[0]["input"][0]["change"]["createNewCompleted"]
    assert change == {
        "startedAndFinishedOnDate": {"startedOn": "2026-10-30T12:00:00+00:00", "timestamp": "2026-11-01T12:00:00+00:00"}
    }


def test_update_progress_needs_something_in_progress(fake: FakeRyot) -> None:
    fake.add(movie("met_1", "Spirited Away"))
    _, t = make(fake)
    with pytest.raises(NotFound):
        run(t.update_progress({"id": "met_1", "percent": 50}))
    fake.mine["met_1"]["inProgress"] = seen(state="IN_PROGRESS", progress="10")
    run(t.update_progress({"id": "met_1", "state": "dropped"}))
    assert fake.ops("DeployBulkMetadataProgressUpdate")[0]["input"][0]["change"] == {"changeLatestState": "DROPPED"}
    with pytest.raises(InvalidArgument, match="exactly one"):
        run(t.update_progress({"id": "met_1", "percent": 5, "state": "dropped"}))


def test_review_converts_the_rating_to_the_user_s_scale(fake: FakeRyot) -> None:
    _, t = make(fake)
    out = run(t.review({"id": "met_1", "rating": 9, "text": "Loved it"}))
    assert out == {"review_id": "rev_new", "id": "met_1", "updated": False}
    sent = fake.ops("CreateOrUpdateReview")[0]["input"]
    assert sent["rating"] == "4.5" and sent["visibility"] == "PRIVATE" and sent["entityLot"] == "METADATA"
    with pytest.raises(InvalidArgument):
        run(t.review({"id": "met_1"}))


def test_add_to_the_watchlist(fake: FakeRyot) -> None:
    _, t = make(fake)
    out = run(t.add_to_collection({"ids": ["met_1", "met_2"]}))
    assert out == {"queued": True, "collection": "Watchlist", "ids": ["met_1", "met_2"]}
    sent = fake.ops("DeployAddEntitiesToCollectionJob")[0]["input"]
    assert sent["creatorUserId"] == "usr_me"
    assert sent["entities"] == [{"entityId": "met_1", "entityLot": "METADATA"}, {"entityId": "met_2", "entityLot": "METADATA"}]


def test_create_collection_is_idempotent(fake: FakeRyot) -> None:
    _, t = make(fake)
    assert run(t.create_collection({"name": "Ghibli"}))["created"] is True
    assert run(t.create_collection({"name": "ghibli"}))["created"] is False
    assert len(fake.ops("CreateOrUpdateCollection")) == 1


def test_ids_are_validated(fake: FakeRyot) -> None:
    _, t = make(fake)
    with pytest.raises(InvalidArgument):
        run(t.get_title({"id": "../etc/passwd"}))
    with pytest.raises(InvalidArgument):
        run(t.add_to_collection({"ids": []}))


def test_search_trims_to_the_limit_ryot_ignores(fake: FakeRyot) -> None:
    for i in range(8):
        fake.add(movie(f"met_{i}", f"Film {i}"))
    fake.search_results = [f"met_{i}" for i in range(8)]
    _, t = make(fake)
    out = run(t.search({"kind": "movie", "query": "film", "limit": 3}))
    assert [r["id"] for r in out["results"]] == ["met_0", "met_1", "met_2"]
    # Only the three kept were looked up.
    assert len(fake.ops("MetadataDetails")) == 3


def test_a_failed_provider_search_says_a_key_may_be_missing(fake: FakeRyot) -> None:
    fake.search_fails = True
    _, t = make(fake)
    with pytest.raises(UpstreamError, match="needs a key") as err:
        run(t.search({"kind": "movie", "query": "x"}))
    assert err.value.detail == {"source": "tmdb"}


def test_get_title_fills_in_a_stub_from_search(fake: FakeRyot) -> None:
    fake.add(movie("met_1", "Frieren", isPartial=True, description=None, genres=[]))
    fake.fill["met_1"] = {"description": "An elf mage outlives her party.", "genres": [{"name": "Fantasy"}]}
    _, t = make(fake)
    out = run(t.get_title({"id": "met_1"}))
    assert out["description"] == "An elf mage outlives her party."
    assert out["genres"] == ["Fantasy"]
    assert "metadata_incomplete" not in out
    assert fake.ops("DeployUpdateMediaEntityJob") == [{"input": {"entityId": "met_1", "entityLot": "METADATA"}}]


def test_a_full_title_is_not_refetched(fake: FakeRyot) -> None:
    fake.add(movie("met_1", "Spirited Away"))
    _, t = make(fake)
    run(t.get_title({"id": "met_1"}))
    assert fake.ops("DeployUpdateMediaEntityJob") == []


def test_plain_text() -> None:
    from ryot_mcp.service import plain_text

    raw = "<p>The adventure is over.<br>Life goes on &amp; on.</p><p><i>(Source: X)</i></p>"
    assert plain_text(raw) == "The adventure is over.\nLife goes on & on.\n(Source: X)"


def test_get_title_names_only_the_roles_worth_naming(fake: FakeRyot) -> None:
    fake.add(
        movie(
            "met_1",
            "Frieren",
            creators=[
                {"name": "ADR Director (English)", "items": [{"idOrName": "Jad Saxton", "isFree": True, "character": None}]},
                {"name": "Director", "items": [{"idOrName": "Keiichirou Saitou", "isFree": True, "character": None}]},
            ],
        )
    )
    _, t = make(fake)
    assert run(t.get_title({"id": "met_1"}))["people"] == {"Director": ["Keiichirou Saitou"]}


def test_where_to_watch_is_one_country(fake: FakeRyot) -> None:
    fake.add(movie("met_1", "Spirited Away"))
    _, t = make(fake)
    out = run(t.get_title({"id": "met_1"}))
    assert (out["where_to_watch"], out["where_to_watch_region"]) == (["Netflix"], "AR")
    assert run(t.get_title({"id": "met_1", "region": "fr"}))["where_to_watch"] == ["Arte"]
    _, everywhere = make(fake, region=None)
    assert run(everywhere.get_title({"id": "met_1"}))["where_to_watch"] == ["Netflix", "Arte"]


def test_tmdb_departments_are_named(fake: FakeRyot) -> None:
    fake.add(
        movie(
            "met_1",
            "Spirited Away",
            creators=[
                {"name": "Visual Effects", "items": [{"idOrName": "Someone", "isFree": True, "character": None}]},
                {"name": "Acting", "items": [{"idOrName": "Rumi Hiiragi", "isFree": True, "character": "Chihiro"}]},
                {"name": "Directing", "items": [{"idOrName": "Hayao Miyazaki", "isFree": True, "character": None}]},
            ],
        )
    )
    _, t = make(fake)
    assert run(t.get_title({"id": "met_1"}))["people"] == {"Directing": ["Hayao Miyazaki"], "Acting": ["Rumi Hiiragi (Chihiro)"]}


def test_a_collection_that_downloads_needs_download_true(fake: FakeRyot) -> None:
    fake.collections.append({"id": "col_d", "name": "Download", "count": 0, "isDefault": False, "description": None})
    fake.integrations = [
        {
            "id": "int_1",
            "name": "Radarr",
            "provider": "RADARR",
            "isDisabled": False,
            "providerSpecifics": {"radarrSyncCollectionIds": ["col_d"]},
        },
        {
            "id": "int_2",
            "name": "Sonarr",
            "provider": "SONARR",
            "isDisabled": False,
            "providerSpecifics": {"sonarrSyncCollectionIds": ["col_d"]},
        },
    ]
    _, t = make(fake)
    with pytest.raises(WouldDownload) as err:
        run(t.add_to_collection({"ids": ["met_1"], "collection": "Download"}))
    assert err.value.detail == {"downloads_to": ["radarr", "sonarr"]}
    assert fake.ops("DeployAddEntitiesToCollectionJob") == []
    out = run(t.add_to_collection({"ids": ["met_1"], "collection": "Download", "download": True}))
    assert out["sent_to"] == ["radarr", "sonarr"]
    # The Watchlist is synced to nothing, and removing never downloads.
    assert "sent_to" not in run(t.add_to_collection({"ids": ["met_1"]}))
    run(t.remove_from_collection({"ids": ["met_1"], "collection": "Download"}))
    # A disabled integration sends nothing.
    fake.integrations[0]["isDisabled"] = fake.integrations[1]["isDisabled"] = True
    assert "sent_to" not in run(t.add_to_collection({"ids": ["met_1"], "collection": "Download"}))
