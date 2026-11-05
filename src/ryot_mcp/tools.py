"""Tool definitions: names, descriptions, input schemas, annotations.

Annotations are honest so a client can ask before the ones that matter:
reads are readOnly; logging a watch or writing a review adds a new entry each
time (not idempotent); collection membership is idempotent; removing a seen
entry or deleting a review is destructive and cannot be undone."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mcp import types

from .service import COLLECTION_SORTS, FILTERS, KINDS, LIST_SORTS, SOURCES, STATES, Tracker

ID = {"type": "string", "minLength": 1, "maxLength": 64, "description": "A title id (met_…) returned by another tool."}
KIND = {"type": "string", "enum": list(KINDS)}
DATE = {
    "type": "string",
    "maxLength": 40,
    "description": "YYYY-MM-DD, or an ISO 8601 timestamp.",
}
PAGE = {"type": "integer", "minimum": 1, "maximum": 1000}
WHERE = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 60},
    "maxItems": 5,
    "description": 'Where it was watched, played or read, e.g. ["Netflix"], ["Jellyfin"], ["Cinema"].',
}
SEASON = {"type": "integer", "minimum": 0, "maximum": 1000}
EPISODE = {"type": "integer", "minimum": 0, "maximum": 100000}
POSITION = {
    "anime_episode": {"type": "integer", "minimum": 0, "maximum": 100000},
    "manga_chapter": {"type": "number", "minimum": 0, "maximum": 100000},
    "manga_volume": {"type": "integer", "minimum": 0, "maximum": 10000},
    "podcast_episode": {"type": "integer", "minimum": 0, "maximum": 100000},
}
COLLECTION = {
    "type": "string",
    "minLength": 1,
    "maxLength": 200,
    "description": "A collection's name from list_collections. Default 'Watchlist'.",
}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


def _ann(title: str, read_only: bool, destructive: bool, idempotent: bool) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        title=title,
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


READ = dict(read_only=True, destructive=False, idempotent=True)


@dataclass(frozen=True)
class ToolSpec:
    tool: types.Tool
    call: Callable[[Tracker, dict[str, Any]], Awaitable[dict[str, Any]]]


def _tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    annotations: types.ToolAnnotations,
    call: Callable[[Tracker, dict[str, Any]], Awaitable[dict[str, Any]]],
) -> ToolSpec:
    return ToolSpec(types.Tool(name=name, description=description, inputSchema=schema, annotations=annotations), call)


TOOLS: list[ToolSpec] = [
    # ── read ────────────────────────────────────────────────────────────
    _tool(
        "search",
        "Find titles of one kind by name: movies, shows, anime, manga, books, games, music, podcasts, audiobooks, "
        "comics or visual novels. Searches Ryot's default source for that kind (TMDB for movies and shows, "
        "AniList for anime and manga, Hardcover or Open Library for books, IGDB for games) unless source names "
        "another. Each result has its title id, title, year and source, and this user's state: how many times "
        "they have seen it, their rating (out of 10), and the collections it is in (Watchlist among them). "
        "Paged: pass next_page as page for more.",
        _schema(
            {
                "kind": KIND,
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "source": {"type": "string", "enum": list(SOURCES)},
                "page": PAGE,
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Default 10."},
            },
            ["kind", "query"],
        ),
        _ann("Search titles", **READ),
        lambda t, a: t.search(a),
    ),
    _tool(
        "get_title",
        "One title in full: year and release date, genres, where it can be streamed (in this server's country, "
        "or region's), the provider's rating, runtime, seasons and episode counts (shows), episodes (anime), "
        "pages (books), the main people (directors, creators, authors, cast), the description, and this user's "
        "side: every time they saw it (each with its seen_id, dates, season and episode, and where), what is in "
        "progress, the next episode, their reviews and ratings (out of 10), and its collections.",
        _schema(
            {
                "id": ID,
                "max_description_chars": {"type": "integer", "minimum": 0, "maximum": 20000, "description": "Default 2000."},
                "region": {
                    "type": "string",
                    "pattern": "^[A-Za-z]{2}$",
                    "description": "A two-letter country code for where_to_watch, e.g. when travelling.",
                },
            },
            ["id"],
        ),
        _ann("Look up a title", **READ),
        lambda t, a: t.get_title(a),
    ),
    _tool(
        "similar",
        "Titles similar to one, from its source's suggestions, each with this user's state.",
        _schema({"id": ID, "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Default 10."}}, ["id"]),
        _ann("Similar titles", **READ),
        lambda t, a: t.similar(a),
    ),
    _tool(
        "trending",
        "What is trending now across Ryot's sources, each with this user's state.",
        _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 30, "description": "Default 10."}}),
        _ann("Trending titles", **READ),
        lambda t, a: t.trending(a),
    ),
    _tool(
        "my_media",
        "The titles this user has interacted with (seen, rated, started, collected), newest consumed first unless "
        "sort says otherwise; optionally one kind, a filter (rated, unrated, dropped, on_hold, unfinished) or a "
        "title search. Paged.",
        _schema(
            {
                "kind": KIND,
                "filter": {"type": "string", "enum": list(FILTERS), "description": "Default 'all'."},
                "sort": {"type": "string", "enum": list(LIST_SORTS), "description": "Default 'last_consumed'."},
                "order": {"type": "string", "enum": ["asc", "desc"], "description": "Default 'desc'."},
                "query": {"type": "string", "maxLength": 200},
                "page": PAGE,
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Default 20."},
            }
        ),
        _ann("My media", **READ),
        lambda t, a: t.my_media(a),
    ),
    _tool(
        "list_collections",
        "This user's collections with how many titles each holds: the defaults (Watchlist, In Progress, Completed, "
        "Monitoring, Owned, Reminders) and any they made.",
        _schema({}),
        _ann("List collections", **READ),
        lambda t, a: t.list_collections(a),
    ),
    _tool(
        "get_collection",
        "The titles in one collection, the Watchlist unless collection names another, in the collection's own "
        "order unless sort says otherwise. Paged.",
        _schema(
            {
                "collection": COLLECTION,
                "sort": {"type": "string", "enum": list(COLLECTION_SORTS), "description": "Default 'rank'."},
                "order": {"type": "string", "enum": ["asc", "desc"]},
                "page": PAGE,
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Default 20."},
            }
        ),
        _ann("Collection contents", **READ),
        lambda t, a: t.get_collection(a),
    ),
    _tool(
        "upcoming",
        "Upcoming releases of what this user follows: new episodes of shows and anime they are watching or "
        "monitoring, and dated releases of titles in their collections, soonest first.",
        _schema(
            {
                "days": {"type": "integer", "minimum": 1, "maximum": 365, "description": "Default 30."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "Default 50."},
            }
        ),
        _ann("Upcoming releases", **READ),
        lambda t, a: t.upcoming(a),
    ),
    # ── write ───────────────────────────────────────────────────────────
    _tool(
        "log_seen",
        "Record that this user finished a title: a movie, a book, a game, an anime or manga (optionally one "
        "episode, chapter or volume), or a show's episode. For a show, season and episode log one episode, season "
        "alone logs every episode of that season, and neither logs every episode of every season (specials "
        "excluded unless include_specials). Dated finished_on, or started_on and finished_on, or date_unknown; "
        "now when none is given. where says where it was watched. Adds a new entry each time it is called, so a "
        "rewatch is a second call; get_title shows the history. Ryot applies it in the background, so a read "
        "straight after may not show it yet.",
        _schema(
            {
                "id": ID,
                "finished_on": DATE,
                "started_on": DATE,
                "date_unknown": {"type": "boolean"},
                "season": SEASON,
                "episode": EPISODE,
                "include_specials": {"type": "boolean"},
                "where": WHERE,
                **POSITION,
            },
            ["id"],
        ),
        _ann("Log as seen", read_only=False, destructive=False, idempotent=False),
        lambda t, a: t.log_seen(a),
    ),
    _tool(
        "start",
        "Record that this user started a title (in progress), from started_on or now. For a show, say which "
        "episode with season and episode.",
        _schema({"id": ID, "started_on": DATE, "season": SEASON, "episode": EPISODE, "where": WHERE, **POSITION}, ["id"]),
        _ann("Start a title", read_only=False, destructive=False, idempotent=False),
        lambda t, a: t.start(a),
    ),
    _tool(
        "update_progress",
        "Change what is in progress for a title: its percent (0-100), or its state (completed, dropped, on_hold). "
        "Exactly one of the two.",
        _schema(
            {
                "id": ID,
                "percent": {"type": "number", "minimum": 0, "maximum": 100},
                "state": {"type": "string", "enum": list(STATES)},
            },
            ["id"],
        ),
        _ann("Update progress", read_only=False, destructive=False, idempotent=True),
        lambda t, a: t.update_progress(a),
    ),
    _tool(
        "remove_seen",
        "Delete one entry from a title's history (a seen_id from get_title): for a watch logged by mistake. Cannot be undone.",
        _schema({"seen_id": {"type": "string", "minLength": 1, "maxLength": 64}}, ["seen_id"]),
        _ann("Remove a seen entry", read_only=False, destructive=True, idempotent=True),
        lambda t, a: t.remove_seen(a),
    ),
    _tool(
        "review",
        "Rate and/or review a title, or one episode of a show (season and episode). rating is out of 10 (halves "
        "allowed), converted to the scale this user picked in Ryot. Private unless public=true. review_id (from "
        "get_title) edits that review instead of adding one.",
        _schema(
            {
                "id": ID,
                "rating": {"type": "number", "minimum": 0, "maximum": 10},
                "text": {"type": "string", "maxLength": 20000},
                "spoiler": {"type": "boolean"},
                "public": {"type": "boolean"},
                "date": DATE,
                "season": SEASON,
                "episode": EPISODE,
                "review_id": {"type": "string", "minLength": 1, "maxLength": 64},
            },
            ["id"],
        ),
        _ann("Rate or review", read_only=False, destructive=False, idempotent=False),
        lambda t, a: t.review(a),
    ),
    _tool(
        "delete_review",
        "Delete one of this user's reviews (a review_id from get_title). Cannot be undone.",
        _schema({"review_id": {"type": "string", "minLength": 1, "maxLength": 64}}, ["review_id"]),
        _ann("Delete a review", read_only=False, destructive=True, idempotent=True),
        lambda t, a: t.delete_review(a),
    ),
    _tool(
        "add_to_collection",
        "Put titles in a collection, the Watchlist unless collection names another. Nothing is downloaded: Ryot "
        "only records it. Already there is fine.",
        _schema(
            {"ids": {"type": "array", "items": ID, "minItems": 1, "maxItems": 50}, "collection": COLLECTION},
            ["ids"],
        ),
        _ann("Add to collection", read_only=False, destructive=False, idempotent=True),
        lambda t, a: t.add_to_collection(a),
    ),
    _tool(
        "remove_from_collection",
        "Take titles out of a collection, the Watchlist unless collection names another. The titles and their history stay.",
        _schema(
            {"ids": {"type": "array", "items": ID, "minItems": 1, "maxItems": 50}, "collection": COLLECTION},
            ["ids"],
        ),
        _ann("Remove from collection", read_only=False, destructive=False, idempotent=True),
        lambda t, a: t.remove_from_collection(a),
    ),
    _tool(
        "create_collection",
        "Make a collection of this user's own. If one with that name exists, returns it (created=false).",
        _schema(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 200},
                "description": {"type": "string", "maxLength": 2000},
            },
            ["name"],
        ),
        _ann("Create a collection", read_only=False, destructive=False, idempotent=True),
        lambda t, a: t.create_collection(a),
    ),
]

BY_NAME = {t.tool.name: t for t in TOOLS}
