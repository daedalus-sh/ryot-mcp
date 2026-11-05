"""What each tool does, in Ryot's terms.

Every title is a Ryot metadata id (`met_…`), the id the other tools take.
Ratings are out of 10 here whatever scale the user picked in Ryot: Ryot
stores them out of 100 and takes them in the user's own scale, and this file
converts both ways. Dates are `YYYY-MM-DD` (noon UTC, so the day survives any
time zone a person is likely to be in) or a full ISO 8601 timestamp."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from . import queries
from .client import Ryot
from .errors import InvalidArgument, NotFound, RyotError, UpstreamError

# Tool vocabulary → Ryot's MediaLot.
KINDS: dict[str, str] = {
    "movie": "MOVIE",
    "show": "SHOW",
    "anime": "ANIME",
    "manga": "MANGA",
    "book": "BOOK",
    "game": "VIDEO_GAME",
    "music": "MUSIC",
    "podcast": "PODCAST",
    "audiobook": "AUDIO_BOOK",
    "comic": "COMIC_BOOK",
    "visual_novel": "VISUAL_NOVEL",
}
LOT_TO_KIND = {v: k for k, v in KINDS.items()}

SOURCES = (
    "tmdb",
    "tvdb",
    "anilist",
    "myanimelist",
    "manga_updates",
    "hardcover",
    "openlibrary",
    "google_books",
    "igdb",
    "giant_bomb",
    "spotify",
    "music_brainz",
    "youtube_music",
    "itunes",
    "listennotes",
    "audible",
    "metron",
    "vndb",
)

STATES = {"completed": "COMPLETED", "dropped": "DROPPED", "on_hold": "ON_A_HOLD"}
FILTERS = {
    "all": "ALL",
    "rated": "RATED",
    "unrated": "UNRATED",
    "dropped": "DROPPED",
    "on_hold": "ON_A_HOLD",
    "unfinished": "UNFINISHED",
}
LIST_SORTS = {
    "last_consumed": "LAST_CONSUMED",
    "last_updated": "LAST_UPDATED",
    "title": "TITLE",
    "my_rating": "USER_RATING",
    "release_date": "RELEASE_DATE",
    "times_consumed": "TIMES_CONSUMED",
    "provider_rating": "PROVIDER_RATING",
    "random": "RANDOM",
}
COLLECTION_SORTS = {
    "rank": "RANK",
    "date_added": "DATE",
    "title": "TITLE",
    "my_rating": "USER_RATING",
    "last_consumed": "LAST_CONSUMED",
    "provider_rating": "PROVIDER_RATING",
    "random": "RANDOM",
}
# The roles worth naming in get_title, in the order they are shown.
ROLES = ("Director", "Creator", "Author", "Writer", "Developer", "Publisher", "Actor", "Voice Actor", "Artist")
MAX_PEOPLE = 12
MAX_EPISODES = 2000
METADATA_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _dec(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


def _rating_out(stored: Any) -> float | None:
    """Ryot's stored rating (out of 100) → out of 10."""
    value = _dec(stored)
    return None if value is None else round(value / 10, 1)


def rating_in(rating: float, scale: str) -> str:
    """Out of 10 → what createOrUpdateReview takes for this user's scale."""
    r = Decimal(str(rating))
    if scale == "OUT_OF_TEN":
        value = r
    elif scale == "OUT_OF_FIVE":
        value = r / 2
    else:  # OUT_OF_HUNDRED, and THREE_POINT_SMILEY, which Ryot takes as out of 100
        value = r * 10
    return format(value.quantize(Decimal("0.01")).normalize(), "f")


def when(value: Any, *, name: str) -> str:
    """A date or timestamp from a tool argument → Ryot's DateTime (RFC 3339, UTC)."""
    if value is None or value == "now":
        return datetime.now(UTC).isoformat(timespec="seconds")
    if not isinstance(value, str):
        raise InvalidArgument(f"{name} must be a date (YYYY-MM-DD) or an ISO 8601 timestamp")
    text = value.strip()
    if _DATE.match(text):
        text += "T12:00:00+00:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidArgument(f"{name} must be a date (YYYY-MM-DD) or an ISO 8601 timestamp") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def _metadata_id(args: dict[str, Any], key: str = "id") -> str:
    value = args.get(key)
    if not isinstance(value, str) or not METADATA_ID.match(value):
        raise InvalidArgument(f"{key} must be a title id returned by another tool")
    return value


def _kind(args: dict[str, Any], *, required: bool) -> str | None:
    kind = args.get("kind")
    if kind is None:
        if required:
            raise InvalidArgument(f"kind is required: one of {', '.join(KINDS)}")
        return None
    if kind not in KINDS:
        raise InvalidArgument(f"kind must be one of {', '.join(KINDS)}")
    return KINDS[kind]


def _page(args: dict[str, Any]) -> int:
    return int(args.get("page") or 1)


def _seen(s: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "seen_id": s.get("id"),
        "state": (s.get("state") or "").lower(),
        "progress_percent": _dec(s.get("progress")),
        "started_on": s.get("startedOn"),
        "finished_on": s.get("finishedOn"),
        "updated_on": s.get("lastUpdatedOn"),
    }
    show = s.get("showExtraInformation")
    if show:
        out["season"], out["episode"] = show.get("season"), show.get("episode")
    anime = s.get("animeExtraInformation")
    if anime and anime.get("episode") is not None:
        out["episode"] = anime["episode"]
    manga = s.get("mangaExtraInformation")
    if manga:
        if manga.get("chapter") is not None:
            out["chapter"] = _dec(manga["chapter"])
        if manga.get("volume") is not None:
            out["volume"] = manga["volume"]
    if s.get("providersConsumedOn"):
        out["where"] = s["providersConsumedOn"]
    return {k: v for k, v in out.items() if v is not None}


class Tracker:
    def __init__(self, ryot: Ryot, *, concurrency: int = 4) -> None:
        self.ryot = ryot
        self._sem = asyncio.Semaphore(concurrency)
        self._sources: dict[str, list[str]] | None = None

    # ── shared reads ───────────────────────────────────────────────────

    async def health(self) -> tuple[bool, str]:
        try:
            me = await self.ryot.me()
        except RyotError as err:
            return False, err.kind
        return True, f"signed in as {me.name}"

    async def _sources_for(self, lot: str) -> list[str]:
        if self._sources is None:
            data = await self.ryot.public(queries.CORE_DETAILS)
            mappings = ((data.get("coreDetails") or {}).get("metadataLotSourceMappings")) or []
            self._sources = {m["lot"]: list(m["sources"]) for m in mappings}
        return self._sources.get(lot, [])

    async def _details(self, metadata_id: str) -> dict[str, Any]:
        async with self._sem:
            data = await self.ryot.call(queries.METADATA_DETAILS, {"metadataId": metadata_id})
        details = (data.get("metadataDetails") or {}).get("response")
        if not details:
            raise NotFound(f"no title with id {metadata_id}")
        return details

    async def _mine(self, metadata_id: str) -> dict[str, Any]:
        async with self._sem:
            data = await self.ryot.call(queries.USER_METADATA_DETAILS, {"metadataId": metadata_id})
        return (data.get("userMetadataDetails") or {}).get("response") or {}

    @staticmethod
    def _my_summary(mine: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"times_seen": mine.get("seenByUserCount", 0)}
        own = [r for r in mine.get("reviews") or [] if user_id is None or (r.get("postedBy") or {}).get("id") == user_id]
        rating = next((_rating_out(r.get("rating")) for r in own if r.get("rating") is not None), None)
        if rating is not None:
            out["my_rating"] = rating
        progress = mine.get("inProgress")
        if progress:
            out["in_progress_percent"] = _dec(progress.get("progress"))
        collections = [c["details"]["collectionName"] for c in mine.get("collections") or [] if c.get("details")]
        if collections:
            out["collections"] = collections
        return out

    async def _brief(self, metadata_id: str, *, status: bool, user_id: str | None = None) -> dict[str, Any]:
        details = await self._details(metadata_id)
        out: dict[str, Any] = {
            "id": details["id"],
            "title": details.get("title"),
            "kind": LOT_TO_KIND.get(details.get("lot", ""), (details.get("lot") or "").lower()),
            "year": details.get("publishYear"),
            "source": (details.get("source") or "").lower(),
        }
        if details.get("providerRating") is not None:
            out["provider_rating"] = _rating_out(details["providerRating"])
        if status:
            out.update(self._my_summary(await self._mine(metadata_id), user_id))
        return {k: v for k, v in out.items() if v is not None}

    async def _briefs(self, ids: list[str], *, status: bool = True) -> list[dict[str, Any]]:
        user_id = (await self.ryot.me()).id if status else None
        return list(await asyncio.gather(*(self._brief(i, status=status, user_id=user_id) for i in ids)))

    # ── read tools ─────────────────────────────────────────────────────

    async def search(self, args: dict[str, Any]) -> dict[str, Any]:
        lot = _kind(args, required=True)
        assert lot is not None
        query = str(args.get("query") or "").strip()
        if not query:
            raise InvalidArgument("query must not be empty")
        available = await self._sources_for(lot)
        source = args.get("source")
        if source is None:
            if not available:
                raise UpstreamError(f"Ryot has no source for {args['kind']} on this server")
            ryot_source = available[0]
        else:
            ryot_source = str(source).upper()
            if available and ryot_source not in available:
                raise InvalidArgument(
                    f"source {source} does not serve {args['kind']}; use one of {', '.join(s.lower() for s in available)}"
                )
        data = await self.ryot.call(
            queries.METADATA_SEARCH,
            {
                "input": {
                    "lot": lot,
                    "source": ryot_source,
                    "search": {"query": query, "page": _page(args), "take": int(args.get("limit") or 10)},
                }
            },
        )
        response = (data.get("metadataSearch") or {}).get("response") or {}
        items = await self._briefs(list(response.get("items") or []))
        details = response.get("details") or {}
        return {
            "source": ryot_source.lower(),
            "results": items,
            "total": details.get("totalItems"),
            "next_page": details.get("nextPage"),
        }

    async def _people(self, creators: list[dict[str, Any]]) -> dict[str, list[str]]:
        by_role = {g["name"]: g.get("items") or [] for g in creators}
        wanted: list[tuple[str, dict[str, Any]]] = []
        for role in [r for r in ROLES if r in by_role] + [r for r in by_role if r not in ROLES]:
            for item in by_role[role][:5]:
                wanted.append((role, item))
        wanted = wanted[: MAX_PEOPLE * 2]

        async def name(item: dict[str, Any]) -> str | None:
            if item.get("isFree"):
                return item.get("idOrName")
            try:
                async with self._sem:
                    data = await self.ryot.call(queries.PERSON_NAME, {"personId": item["idOrName"]})
            except RyotError:
                return None
            return (((data.get("personDetails") or {}).get("response") or {}).get("details") or {}).get("name")

        resolved = await asyncio.gather(*(name(item) for _, item in wanted[:MAX_PEOPLE]))
        out: dict[str, list[str]] = {}
        for (role, item), who in zip(wanted[:MAX_PEOPLE], resolved, strict=True):
            if who:
                label = f"{who} ({item['character']})" if item.get("character") else who
                out.setdefault(role.lower().replace(" ", "_"), []).append(label)
        return out

    async def get_title(self, args: dict[str, Any]) -> dict[str, Any]:
        metadata_id = _metadata_id(args)
        details, mine, me = await asyncio.gather(self._details(metadata_id), self._mine(metadata_id), self.ryot.me())
        limit = int(args.get("max_description_chars", 2000))
        description = details.get("description") or ""
        out: dict[str, Any] = {
            "id": details["id"],
            "title": details.get("title"),
            "kind": LOT_TO_KIND.get(details.get("lot", ""), (details.get("lot") or "").lower()),
            "year": details.get("publishYear"),
            "release_date": details.get("publishDate"),
            "source": (details.get("source") or "").lower(),
            "source_url": details.get("sourceUrl"),
            "provider_rating": _rating_out(details.get("providerRating")),
            "status": details.get("productionStatus"),
            "language": details.get("originalLanguage"),
            "genres": [g["name"] for g in details.get("genres") or []],
            "where_to_watch": [w["name"] for w in details.get("watchProviders") or []],
            "description": description[:limit] if limit else None,
            "metadata_incomplete": bool(details.get("isPartial")) or None,
        }
        if len(description) > limit > 0:
            out["description_truncated"] = True
        show = details.get("showSpecifics")
        if show:
            out["runtime_minutes"] = show.get("runtime")
            out["seasons"] = [
                {"season": s["seasonNumber"], "name": s.get("name"), "episodes": len(s.get("episodes") or [])}
                for s in show.get("seasons") or []
            ]
            out["total_episodes"] = show.get("totalEpisodes")
        for key, field, dest in (
            ("movieSpecifics", "runtime", "runtime_minutes"),
            ("animeSpecifics", "episodes", "episodes"),
            ("bookSpecifics", "pages", "pages"),
            ("mangaSpecifics", "volumes", "volumes"),
        ):
            spec = details.get(key)
            if spec and spec.get(field) is not None:
                out[dest] = spec[field]
        manga = details.get("mangaSpecifics")
        if manga and manga.get("chapters") is not None:
            out["chapters"] = _dec(manga["chapters"])
        if details.get("creators"):
            out["people"] = await self._people(details["creators"])
        own_reviews = [r for r in mine.get("reviews") or [] if (r.get("postedBy") or {}).get("id") == me.id]
        mine_out: dict[str, Any] = {
            "times_seen": mine.get("seenByUserCount", 0),
            "average_rating": _rating_out(mine.get("averageRating")),
            "in_progress": _seen(mine["inProgress"]) if mine.get("inProgress") else None,
            "history": [_seen(s) for s in mine.get("history") or []],
            "next": {k: v for k, v in (mine.get("nextEntry") or {}).items() if v is not None} or None,
            "reviews": [
                {
                    k: v
                    for k, v in {
                        "review_id": r.get("id"),
                        "rating": _rating_out(r.get("rating")),
                        "text": r.get("textOriginal"),
                        "spoiler": r.get("isSpoiler"),
                        "visibility": (r.get("visibility") or "").lower(),
                        "posted_on": r.get("postedOn"),
                        "season": (r.get("showExtraInformation") or {}).get("season"),
                        "episode": (r.get("showExtraInformation") or {}).get("episode"),
                    }.items()
                    if v is not None
                }
                for r in own_reviews
            ],
            "collections": [c["details"]["collectionName"] for c in mine.get("collections") or [] if c.get("details")],
            "reasons": [r.lower() for r in mine.get("mediaReason") or []],
        }
        out["me"] = {k: v for k, v in mine_out.items() if v not in (None, [])}
        out["similar_available"] = len(details.get("suggestions") or [])
        return {k: v for k, v in out.items() if v not in (None, [], "")}

    async def similar(self, args: dict[str, Any]) -> dict[str, Any]:
        details = await self._details(_metadata_id(args))
        ids = list(details.get("suggestions") or [])[: int(args.get("limit") or 10)]
        return {"for": details.get("title"), "similar": await self._briefs(ids)}

    async def trending(self, args: dict[str, Any]) -> dict[str, Any]:
        data = await self.ryot.call(queries.TRENDING)
        ids = list(data.get("trendingMetadata") or [])[: int(args.get("limit") or 10)]
        return {"trending": await self._briefs(ids)}

    async def _collections(self) -> list[dict[str, Any]]:
        data = await self.ryot.call(queries.COLLECTIONS)
        return list((data.get("userCollectionsList") or {}).get("response") or [])

    async def list_collections(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "collections": [
                {
                    k: v
                    for k, v in {
                        "collection_id": c["id"],
                        "name": c["name"],
                        "count": c.get("count"),
                        "default": c.get("isDefault"),
                        "description": c.get("description"),
                    }.items()
                    if v is not None
                }
                for c in await self._collections()
            ]
        }

    async def _collection(self, name: str) -> dict[str, Any]:
        collections = await self._collections()
        for c in collections:
            if c["name"].casefold() == name.casefold():
                return c
        raise NotFound(f"no collection named {name!r}", available=[c["name"] for c in collections])

    async def get_collection(self, args: dict[str, Any]) -> dict[str, Any]:
        collection = await self._collection(str(args.get("collection") or "Watchlist"))
        sort = args.get("sort") or "rank"
        if sort not in COLLECTION_SORTS:
            raise InvalidArgument(f"sort must be one of {', '.join(COLLECTION_SORTS)}")
        order = (args.get("order") or ("asc" if sort in ("rank", "title") else "desc")).upper()
        data = await self.ryot.call(
            queries.COLLECTION_CONTENTS,
            {
                "input": {
                    "collectionId": collection["id"],
                    "search": {"page": _page(args), "take": int(args.get("limit") or 20)},
                    "sort": {"by": COLLECTION_SORTS[sort], "order": order},
                }
            },
        )
        response = (data.get("collectionContents") or {}).get("response") or {}
        results = response.get("results") or {}
        items = results.get("items") or []
        titles = [i["entityId"] for i in items if i.get("entityLot") == "METADATA"]
        briefs = {b["id"]: b for b in await self._briefs(titles)}
        out_items = [
            briefs.get(i["entityId"]) or {"entity_id": i["entityId"], "entity_type": (i.get("entityLot") or "").lower()}
            for i in items
        ]
        return {
            "collection": collection["name"],
            "total": response.get("totalItems"),
            "items": out_items,
            "next_page": (results.get("details") or {}).get("nextPage"),
        }

    async def my_media(self, args: dict[str, Any]) -> dict[str, Any]:
        lot = _kind(args, required=False)
        general = args.get("filter") or "all"
        if general not in FILTERS:
            raise InvalidArgument(f"filter must be one of {', '.join(FILTERS)}")
        sort = args.get("sort") or "last_consumed"
        if sort not in LIST_SORTS:
            raise InvalidArgument(f"sort must be one of {', '.join(LIST_SORTS)}")
        payload: dict[str, Any] = {
            "filter": {"general": FILTERS[general]},
            "search": {"page": _page(args), "take": int(args.get("limit") or 20)},
            "sort": {"by": LIST_SORTS[sort], "order": (args.get("order") or "desc").upper()},
        }
        if lot:
            payload["lot"] = lot
        query = str(args.get("query") or "").strip()
        if query:
            payload["search"]["query"] = query
        data = await self.ryot.call(queries.USER_METADATA_LIST, {"input": payload})
        response = (data.get("userMetadataList") or {}).get("response") or {}
        details = response.get("details") or {}
        return {
            "items": await self._briefs(list(response.get("items") or [])),
            "total": details.get("totalItems"),
            "next_page": details.get("nextPage"),
        }

    async def upcoming(self, args: dict[str, Any]) -> dict[str, Any]:
        data = await self.ryot.call(queries.UPCOMING, {"input": {"nextDays": int(args.get("days") or 30)}})
        events = list(data.get("userUpcomingCalendarEvents") or [])[: int(args.get("limit") or 50)]
        ids = sorted({e["metadataId"] for e in events})
        titles = {b["id"]: b for b in await self._briefs(ids, status=False)}
        out = []
        for e in events:
            brief = titles.get(e["metadataId"], {})
            row: dict[str, Any] = {
                "date": e.get("date"),
                "id": e["metadataId"],
                "title": brief.get("title"),
                "kind": brief.get("kind"),
            }
            show = e.get("showExtraInformation")
            if show:
                row["season"], row["episode"] = show.get("season"), show.get("episode")
            anime = e.get("animeExtraInformation")
            if anime and anime.get("episode") is not None:
                row["episode"] = anime["episode"]
            out.append({k: v for k, v in row.items() if v is not None})
        return {"upcoming": out}

    # ── write tools ────────────────────────────────────────────────────

    @staticmethod
    def _position(args: dict[str, Any]) -> dict[str, Any]:
        position: dict[str, Any] = {}
        if args.get("manga_chapter") is not None:
            position["mangaChapterNumber"] = str(args["manga_chapter"])
        if args.get("manga_volume") is not None:
            position["mangaVolumeNumber"] = args["manga_volume"]
        if args.get("anime_episode") is not None:
            position["animeEpisodeNumber"] = args["anime_episode"]
        if args.get("podcast_episode") is not None:
            position["podcastEpisodeNumber"] = args["podcast_episode"]
        where = args.get("where")
        if where:
            position["providersConsumedOn"] = list(where)
        return position

    async def _episodes(self, details: dict[str, Any], args: dict[str, Any]) -> list[tuple[int, int]]:
        """The (season, episode) pairs a show entry covers."""
        season, episode = args.get("season"), args.get("episode")
        if episode is not None and season is None:
            raise InvalidArgument("episode needs season too")
        if season is not None and episode is not None:
            return [(season, episode)]
        seasons = (details.get("showSpecifics") or {}).get("seasons") or []
        if season is not None:
            match = [s for s in seasons if s["seasonNumber"] == season]
            if not match:
                raise NotFound(f"{details.get('title')} has no season {season}", seasons=[s["seasonNumber"] for s in seasons])
            chosen = match
        else:
            chosen = [s for s in seasons if s["seasonNumber"] > 0 or args.get("include_specials")]
        pairs = [(s["seasonNumber"], e["episodeNumber"]) for s in chosen for e in s.get("episodes") or []]
        if not pairs:
            raise NotFound(f"Ryot lists no episodes for {details.get('title')} yet; try again after its details load")
        if len(pairs) > MAX_EPISODES:
            raise InvalidArgument(f"that is {len(pairs)} episodes; log one season at a time")
        return pairs

    async def log_seen(self, args: dict[str, Any]) -> dict[str, Any]:
        metadata_id = _metadata_id(args)
        details = await self._details(metadata_id)
        position = self._position(args)
        started, finished = args.get("started_on"), args.get("finished_on")
        if args.get("date_unknown"):
            if started or finished:
                raise InvalidArgument("date_unknown cannot be combined with started_on or finished_on")
            variant, base = "withoutDates", {}
        elif started and finished:
            variant, base = (
                "startedAndFinishedOnDate",
                {"startedOn": when(started, name="started_on"), "timestamp": when(finished, name="finished_on")},
            )
        elif started:
            variant, base = "startedOnDate", {"timestamp": when(started, name="started_on")}
        else:
            variant, base = "finishedOnDate", {"timestamp": when(finished, name="finished_on")}
        if details.get("lot") == "SHOW":
            pairs = await self._episodes(details, args)
            entries = [{**base, **position, "showSeasonNumber": s, "showEpisodeNumber": e} for s, e in pairs]
        else:
            if args.get("season") is not None or args.get("episode") is not None:
                raise InvalidArgument("season and episode are for shows; use anime_episode for anime")
            entries = [{**base, **position}]
        await self._progress([{"metadataId": metadata_id, "change": {"createNewCompleted": {variant: e}}} for e in entries])
        return {"queued": True, "id": metadata_id, "title": details.get("title"), "entries": len(entries)}

    async def start(self, args: dict[str, Any]) -> dict[str, Any]:
        metadata_id = _metadata_id(args)
        details = await self._details(metadata_id)
        change: dict[str, Any] = {"startedOn": when(args.get("started_on"), name="started_on"), **self._position(args)}
        if details.get("lot") == "SHOW":
            if args.get("season") is None or args.get("episode") is None:
                raise InvalidArgument("for a show, say which episode was started: season and episode")
            change["showSeasonNumber"], change["showEpisodeNumber"] = args["season"], args["episode"]
        await self._progress([{"metadataId": metadata_id, "change": {"createNewInProgress": change}}])
        return {"queued": True, "id": metadata_id, "title": details.get("title")}

    async def update_progress(self, args: dict[str, Any]) -> dict[str, Any]:
        metadata_id = _metadata_id(args)
        percent, state = args.get("percent"), args.get("state")
        if (percent is None) == (state is None):
            raise InvalidArgument("give exactly one of percent or state")
        mine = await self._mine(metadata_id)
        if not mine.get("inProgress"):
            raise NotFound("nothing is in progress for this title; use start or log_seen")
        if state is not None:
            if state not in STATES:
                raise InvalidArgument(f"state must be one of {', '.join(STATES)}")
            change: dict[str, Any] = {"changeLatestState": STATES[state]}
        else:
            change = {"changeLatestInProgress": str(percent)}
        await self._progress([{"metadataId": metadata_id, "change": change}])
        return {"queued": True, "id": metadata_id}

    async def _progress(self, inputs: list[dict[str, Any]]) -> None:
        data = await self.ryot.call(queries.PROGRESS_UPDATE, {"input": inputs})
        if data.get("deployBulkMetadataProgressUpdate") is not True:
            raise UpstreamError("Ryot did not queue the update")

    async def remove_seen(self, args: dict[str, Any]) -> dict[str, Any]:
        seen_id = _metadata_id(args, "seen_id")
        await self.ryot.call(queries.DELETE_SEEN, {"seenId": seen_id})
        return {"removed": seen_id}

    async def review(self, args: dict[str, Any]) -> dict[str, Any]:
        metadata_id = _metadata_id(args)
        rating, text = args.get("rating"), args.get("text")
        if rating is None and not text:
            raise InvalidArgument("give a rating, a text, or both")
        me = await self.ryot.me()
        payload: dict[str, Any] = {
            "entityId": metadata_id,
            "entityLot": "METADATA",
            "visibility": "PUBLIC" if args.get("public") else "PRIVATE",
            "isSpoiler": bool(args.get("spoiler")),
        }
        if rating is not None:
            payload["rating"] = rating_in(float(rating), me.review_scale)
        if text:
            payload["text"] = str(text)
        if args.get("review_id"):
            payload["reviewId"] = _metadata_id(args, "review_id")
        if args.get("date"):
            payload["date"] = when(args["date"], name="date")
        if args.get("season") is not None:
            payload["showSeasonNumber"] = args["season"]
        if args.get("episode") is not None:
            payload["showEpisodeNumber"] = args["episode"]
        data = await self.ryot.call(queries.REVIEW, {"input": payload})
        review_id = ((data.get("createOrUpdateReview") or {}).get("id")) or None
        return {"review_id": review_id, "id": metadata_id, "updated": bool(args.get("review_id"))}

    async def delete_review(self, args: dict[str, Any]) -> dict[str, Any]:
        review_id = _metadata_id(args, "review_id")
        data = await self.ryot.call(queries.DELETE_REVIEW, {"reviewId": review_id})
        if data.get("deleteReview") is not True:
            raise UpstreamError("Ryot did not delete the review")
        return {"deleted": review_id}

    async def _change_collection(self, args: dict[str, Any], document: str, field: str) -> dict[str, Any]:
        ids = args.get("ids") or []
        if not ids:
            raise InvalidArgument("ids must list at least one title id")
        for i in ids:
            _metadata_id({"id": i})
        collection = await self._collection(str(args.get("collection") or "Watchlist"))
        me = await self.ryot.me()
        data = await self.ryot.call(
            document,
            {
                "input": {
                    "creatorUserId": me.id,
                    "collectionName": collection["name"],
                    "entities": [{"entityId": i, "entityLot": "METADATA"} for i in ids],
                }
            },
        )
        if data.get(field) is not True:
            raise UpstreamError("Ryot did not queue the change")
        return {"queued": True, "collection": collection["name"], "ids": ids}

    async def add_to_collection(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._change_collection(args, queries.ADD_TO_COLLECTION, "deployAddEntitiesToCollectionJob")

    async def remove_from_collection(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._change_collection(args, queries.REMOVE_FROM_COLLECTION, "deployRemoveEntitiesFromCollectionJob")

    async def create_collection(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "").strip()
        if not name:
            raise InvalidArgument("name must not be empty")
        for c in await self._collections():
            if c["name"].casefold() == name.casefold():
                return {"collection_id": c["id"], "name": c["name"], "created": False}
        payload: dict[str, Any] = {"name": name}
        if args.get("description"):
            payload["description"] = str(args["description"])
        data = await self.ryot.call(queries.CREATE_COLLECTION, {"input": payload})
        return {"collection_id": (data.get("createOrUpdateCollection") or {}).get("id"), "name": name, "created": True}
