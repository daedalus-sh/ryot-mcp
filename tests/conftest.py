"""A fake Ryot: answers the named GraphQL operations this server sends, with
Ryot's own error strings, and checks the session on every call."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from ryot_mcp.client import Ryot
from ryot_mcp.config import Settings
from ryot_mcp.service import Tracker

BASE = "http://ryot.test:8000"
TOKEN = "test-bearer-token-0123456789"
SEED = "seed-session"

_OP = re.compile(r"\b(?:query|mutation)\s+(\w+)")


def settings(seed: str | None = SEED, state_dir: Path | None = None, renew_days: int = 30) -> Settings:
    return Settings(
        token=TOKEN,
        listen_host="127.0.0.1",
        listen_port=8080,
        url=BASE,
        seed_session=seed,
        state_dir=state_dir,
        renew_after_days=renew_days,
    )


def movie(mid: str, title: str, year: int = 2001, **extra: Any) -> dict[str, Any]:
    doc = {
        "id": mid,
        "title": title,
        "lot": "MOVIE",
        "source": "TMDB",
        "identifier": mid.removeprefix("met_"),
        "isPartial": False,
        "publishYear": year,
        "publishDate": f"{year}-05-01",
        "description": f"{title}, a film.",
        "providerRating": "78.5",
        "productionStatus": "Released",
        "originalLanguage": "en",
        "sourceUrl": f"https://www.themoviedb.org/movie/{mid}",
        "suggestions": [],
        "genres": [{"name": "Drama"}],
        "watchProviders": [{"name": "Netflix", "languages": ["AR", "US"]}, {"name": "Arte", "languages": ["FR"]}],
        "creators": [],
        "showSpecifics": None,
        "movieSpecifics": {"runtime": 110},
        "animeSpecifics": None,
        "mangaSpecifics": None,
        "bookSpecifics": None,
    }
    doc.update(extra)
    return doc


def show(mid: str, title: str, seasons: dict[int, int]) -> dict[str, Any]:
    return movie(
        mid,
        title,
        lot="SHOW",
        movieSpecifics=None,
        showSpecifics={
            "runtime": 45,
            "totalSeasons": len(seasons),
            "totalEpisodes": sum(seasons.values()),
            "seasons": [
                {"seasonNumber": s, "name": f"Season {s}", "episodes": [{"episodeNumber": e} for e in range(1, n + 1)]}
                for s, n in seasons.items()
            ],
        },
    )


def empty_mine() -> dict[str, Any]:
    return {
        "seenByUserCount": 0,
        "averageRating": None,
        "mediaReason": None,
        "inProgress": None,
        "history": [],
        "nextEntry": None,
        "reviews": [],
        "collections": [],
    }


@dataclass
class FakeRyot:
    sessions: set[str] = field(default_factory=lambda: {SEED})
    user_id: str = "usr_me"
    scale: str = "OUT_OF_FIVE"
    metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    mine: dict[str, dict[str, Any]] = field(default_factory=dict)
    search_results: list[str] = field(default_factory=list)
    collections: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"id": "col_w", "name": "Watchlist", "count": 1, "isDefault": True, "description": None},
            {"id": "col_o", "name": "Owned", "count": 0, "isDefault": True, "description": None},
        ]
    )
    collection_items: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    trending: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    people: dict[str, str] = field(default_factory=dict)
    minted: int = 0
    calls: list[tuple[str, dict[str, Any], str | None]] = field(default_factory=list)
    down: bool = False
    search_fails: bool = False
    # What the fill-in job adds to a stub, by id.
    fill: dict[str, dict[str, Any]] = field(default_factory=dict)
    integrations: list[dict[str, Any]] = field(default_factory=list)
    platforms: list[dict[str, Any]] = field(default_factory=list)

    def add(self, details: dict[str, Any], mine: dict[str, Any] | None = None) -> None:
        self.metadata[details["id"]] = details
        self.mine[details["id"]] = mine or empty_mine()

    def ops(self, name: str) -> list[dict[str, Any]]:
        return [v for op, v, _ in self.calls if op == name]

    # ── the handler ────────────────────────────────────────────────────

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        assert request.url.path == "/backend/graphql"
        body = json.loads(request.content)
        match = _OP.search(body["query"])
        assert match, body["query"]
        op = match.group(1)
        auth = request.headers.get("authorization")
        token = auth.removeprefix("Bearer ") if auth else None
        variables = body.get("variables") or {}
        self.calls.append((op, copy.deepcopy(variables), token))
        authed = token in self.sessions
        if op == "CoreDetails":
            return self._data(
                {
                    "coreDetails": {
                        "version": "v10.5.6",
                        "metadataLotSourceMappings": [
                            {"lot": "MOVIE", "sources": ["TMDB", "TVDB"]},
                            {"lot": "SHOW", "sources": ["TMDB", "TVDB"]},
                            {"lot": "ANIME", "sources": ["ANILIST", "MYANIMELIST"]},
                            {"lot": "BOOK", "sources": ["HARDCOVER", "OPENLIBRARY"]},
                        ],
                    }
                }
            )
        if op == "UserDetails":
            if not authed:
                return self._data({"userDetails": {"__typename": "UserDetailsError", "error": "SESSION_INVALID"}})
            return self._data(
                {
                    "userDetails": {
                        "__typename": "UserDetails",
                        "id": self.user_id,
                        "name": "santi",
                        "preferences": {"general": {"reviewScale": self.scale}},
                    }
                }
            )
        if not authed:
            return self._error("NO_USER_ID")
        return getattr(self, f"op_{op}")(variables)

    @staticmethod
    def _data(data: dict[str, Any]) -> httpx.Response:
        return httpx.Response(200, json={"data": data})

    @staticmethod
    def _error(message: str) -> httpx.Response:
        return httpx.Response(200, json={"data": None, "errors": [{"message": message}]})

    # ── operations ─────────────────────────────────────────────────────

    def op_GenerateAuthToken(self, _: dict[str, Any]) -> httpx.Response:
        self.minted += 1
        token = f"minted-{self.minted}"
        self.sessions.add(token)
        return self._data({"generateAuthToken": token})

    def op_DeployUpdateMediaEntityJob(self, v: dict[str, Any]) -> httpx.Response:
        # Ryot's job fetches the rest from the source; here it is instant.
        details = self.metadata.get(v["input"]["entityId"])
        if details is not None:
            details.update(self.fill.get(details["id"], {}), isPartial=False)
        return self._data({"deployUpdateMediaEntityJob": True})

    def op_MetadataSearch(self, v: dict[str, Any]) -> httpx.Response:
        if self.search_fails:
            return self._error("Failed to search metadata")
        # Like Ryot: the provider's whole page, whatever `take` says.
        return self._data(
            {
                "metadataSearch": {
                    "response": {
                        "items": self.search_results,
                        "details": {"totalItems": len(self.search_results), "nextPage": None},
                    }
                }
            }
        )

    def op_MetadataDetails(self, v: dict[str, Any]) -> httpx.Response:
        details = self.metadata.get(v["metadataId"])
        if details is None:
            return self._error("Metadata with the given ID was not found")
        return self._data({"metadataDetails": {"response": details}})

    def op_UserMetadataDetails(self, v: dict[str, Any]) -> httpx.Response:
        return self._data({"userMetadataDetails": {"response": self.mine.get(v["metadataId"], empty_mine())}})

    def op_PersonDetails(self, v: dict[str, Any]) -> httpx.Response:
        return self._data({"personDetails": {"response": {"details": {"name": self.people.get(v["personId"], "?")}}}})

    def op_UserCollectionsList(self, _: dict[str, Any]) -> httpx.Response:
        return self._data({"userCollectionsList": {"response": self.collections}})

    def op_CollectionContents(self, v: dict[str, Any]) -> httpx.Response:
        cid = v["input"]["collectionId"]
        items = self.collection_items.get(cid, [])
        name = next(c["name"] for c in self.collections if c["id"] == cid)
        return self._data(
            {
                "collectionContents": {
                    "response": {
                        "totalItems": len(items),
                        "details": {"id": cid, "name": name, "description": None},
                        "results": {"items": items, "details": {"totalItems": len(items), "nextPage": None}},
                    }
                }
            }
        )

    def op_UserIntegrations(self, _: dict[str, Any]) -> httpx.Response:
        return self._data(
            {"userIntegrations": [{k: i[k] for k in ("id", "name", "provider", "isDisabled")} for i in self.integrations]}
        )

    def op_CreateOrUpdateUserIntegration(self, v: dict[str, Any]) -> httpx.Response:
        given = v["input"]
        if given.get("integrationId"):
            current = next(i for i in self.integrations if i["id"] == given["integrationId"])
            current.update({k: v for k, v in given.items() if k != "integrationId"})
        else:
            self.integrations.append({**given, "id": f"int_{len(self.integrations) + 1}"})
        return self._data({"createOrUpdateUserIntegration": True})

    def op_UserNotificationPlatforms(self, _: dict[str, Any]) -> httpx.Response:
        return self._data({"userNotificationPlatforms": self.platforms})

    def op_CreateUserNotificationPlatform(self, v: dict[str, Any]) -> httpx.Response:
        given = v["input"]
        new = {"id": f"ntf_{len(self.platforms) + 1}", "lot": given["lot"], "description": given["apiToken"], "isDisabled": None}
        self.platforms.append(new)
        return self._data({"createUserNotificationPlatform": new["id"]})

    def op_DeployAddEntitiesToCollectionJob(self, _: dict[str, Any]) -> httpx.Response:
        return self._data({"deployAddEntitiesToCollectionJob": True})

    def op_DeployRemoveEntitiesFromCollectionJob(self, _: dict[str, Any]) -> httpx.Response:
        return self._data({"deployRemoveEntitiesFromCollectionJob": True})

    def op_CreateOrUpdateCollection(self, v: dict[str, Any]) -> httpx.Response:
        new = {
            "id": f"col_{len(self.collections)}",
            "name": v["input"]["name"],
            "count": 0,
            "isDefault": False,
            "description": None,
        }
        self.collections.append(new)
        return self._data({"createOrUpdateCollection": {"id": new["id"]}})

    def op_UserMetadataList(self, _: dict[str, Any]) -> httpx.Response:
        ids = list(self.metadata)
        return self._data(
            {"userMetadataList": {"response": {"items": ids, "details": {"totalItems": len(ids), "nextPage": None}}}}
        )

    def op_UserUpcomingCalendarEvents(self, _: dict[str, Any]) -> httpx.Response:
        return self._data({"userUpcomingCalendarEvents": self.events})

    def op_TrendingMetadata(self, _: dict[str, Any]) -> httpx.Response:
        return self._data({"trendingMetadata": self.trending})

    def op_DeployBulkMetadataProgressUpdate(self, _: dict[str, Any]) -> httpx.Response:
        return self._data({"deployBulkMetadataProgressUpdate": True})

    def op_DeleteSeenItem(self, v: dict[str, Any]) -> httpx.Response:
        return self._data({"deleteSeenItem": {"id": v["seenId"]}})

    def op_CreateOrUpdateReview(self, v: dict[str, Any]) -> httpx.Response:
        return self._data({"createOrUpdateReview": {"id": v["input"].get("reviewId") or "rev_new"}})

    def op_DeleteReview(self, _: dict[str, Any]) -> httpx.Response:
        return self._data({"deleteReview": True})


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture()
def fake() -> FakeRyot:
    return FakeRyot()


@pytest.fixture()
def clock() -> Clock:
    return Clock()


def make(fake: FakeRyot, clock: Clock | None = None, region: str | None = "AR", **kw: Any) -> tuple[Ryot, Tracker]:
    ryot = Ryot(settings(**kw), transport=httpx.MockTransport(fake.handler), clock=clock or Clock())
    return ryot, Tracker(ryot, fill_wait=0, watch_region=region)


def _synced(fake: FakeRyot, _: dict[str, Any]) -> httpx.Response:
    return fake._data(
        {
            "userIntegrations": [
                {"provider": i["provider"], "isDisabled": i.get("isDisabled"), "providerSpecifics": i.get("providerSpecifics")}
                for i in fake.integrations
            ]
        }
    )


FakeRyot.op_SyncedCollections = _synced  # type: ignore[attr-defined]
