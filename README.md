# ryot-mcp

An [MCP](https://modelcontextprotocol.io) server that gives an AI assistant
your [Ryot](https://github.com/IgnisDa/ryot), the self-hosted tracker for
what you watch, play and read: find a movie, a show, an anime, a book or a
game, see what you thought of it and when you saw it, log a watch from
anywhere (a cinema, Netflix, your own server), rate and review it, and keep
your Watchlist.

It signs in to Ryot as one user, with a session that user minted, and keeps
that session alive by itself. It speaks MCP over streamable HTTP behind a
bearer token. It touches no other user's data, but a public review is visible
to them.

**One tool can start a download.** When a Ryot integration sends a
collection to Radarr or Sonarr, adding a title to that collection makes them
search for it and download it. `add_to_collection` refuses such a collection
(`would_download`) unless the call passes `download: true`.

Written against Ryot v10 (tested with 10.5.6). Ryot v11 is a rewrite with a
new API; this server will need a new release for it.

## Tools

Read

| Tool | |
|---|---|
| `search` | Titles of one kind by name, from Ryot's default source for it (TMDB, AniList, Hardcover or Open Library, IGDB…) or one you name; each with your state. |
| `get_title` | One title in full: year, genres, where to stream it, runtime, seasons, the main people, the description; and your side: every time you saw it, what is in progress, your reviews, its collections. |
| `similar` | Titles like one, from its source's suggestions. |
| `trending` | What is trending now. |
| `my_media` | What you have seen, rated, started or collected; by kind, filter (rated, unrated, dropped, on hold, unfinished), sort, or title. |
| `list_collections` | Your collections and their sizes. |
| `get_collection` | The titles in one, the Watchlist by default. |
| `upcoming` | New episodes and releases of what you follow. |

Write

| Tool | |
|---|---|
| `log_seen` | You finished it: dated, or undated, with where. For a show: an episode, a whole season, or every episode at once. |
| `start` | You started it. |
| `update_progress` | What is in progress: a percent, or completed, dropped or on hold. |
| `review` | A rating out of 10 (converted to your Ryot scale) and/or a text; private unless asked; one review can be edited. |
| `add_to_collection`, `remove_from_collection` | Watchlist (or any collection) membership. Idempotent. Adding to a collection Ryot sends to Radarr or Sonarr downloads the title, so it needs `download: true`. |
| `create_collection` | A collection of your own; returns the existing one if the name is taken. |
| `remove_seen`, `delete_review` | Undo a wrong entry. Destructive. |

Annotations are honest: `readOnlyHint` on reads; `idempotentHint` false on
`log_seen`, `start` and `review`, each of which adds an entry every call; and
`destructiveHint` on the two that delete, and `openWorldHint` on `add_to_collection`, the one that can start a download. Ryot applies progress and
collection changes in the background, so a read straight after one may not
show it yet.

Ratings are out of 10 in and out of every tool, whatever scale you chose in
Ryot. Dates are `YYYY-MM-DD` (taken as noon UTC, so the day is right in any
time zone a person is likely to be in) or full ISO 8601 timestamps.

## The session

Ryot authenticates with a session token, and a session lasts
`USERS_TOKEN_VALID_FOR_DAYS` (90 by default) from when it was minted, however
often it is used. So the server holds its own:

1. You mint one in Ryot: **Settings › Integrations › Create API token**.
2. You hand it over with `ryot-mcp login` (it asks without echo, or reads
   stdin), or as `RYOT_SESSION` in the environment.
3. The server checks it and at once mints a fresh one from it with
   `generateAuthToken`, so it knows its age. With `RYOT_STATE_DIR` set, it
   saves that one (`session.json`, mode 0600) and prefers it from then on;
   a running server picks it up on its next call.
4. Every 6 hours it checks, and once the session it holds is
   `RYOT_RENEW_AFTER_DAYS` old (30 by default), mints the next.

So the token you minted is used once. If the server was stopped longer than
a session lasts, mint a new one and log in again. `ryot-mcp status` says
whether a saved session exists, how old it is and whether Ryot accepts it,
without printing it; `GET /healthz` answers `ok` only while Ryot accepts it.

## Configuration

| Variable | |
|---|---|
| `MCP_BEARER_TOKEN` | Required, 16+ characters. Every request but `GET /healthz` must carry `Authorization: Bearer <it>`. |
| `RYOT_URL` | Required. Ryot's base URL, e.g. `http://ryot:8000`. |
| `RYOT_SESSION` | The session you minted. Optional once a saved one exists. |
| `RYOT_STATE_DIR` | A writable directory for the renewed session. Without it, renewal happens in memory only. |
| `RYOT_RENEW_AFTER_DAYS` | 1 to 80, default 30. |
| `MCP_LISTEN` | Default `0.0.0.0:8080`. MCP is at `/mcp`. |

## Run

```
nix run github:santiagotoscanini/ryot-mcp
```

or install the package (`pip install .`, Python 3.12+) and run `ryot-mcp`.

## Develop

```
nix flake check      # the test suite (in the package's build) and ruff
```

## Licence

MIT.
