"""`ryot-mcp apply`: converge this user's Ryot setup from a spec.

The setup that lives in Ryot's database rather than its configuration:
collections, integrations (Radarr and Sonarr pushes, the Jellyfin sink) and
email notifications. A host that wants them declarative feeds a spec on
stdin at every rebuild; this makes Ryot match it through Ryot's own API, as
the user this server is signed in as, and reports what it did. Only what the
spec names is touched: nothing else is deleted or changed.

    {
      "collections": [{"name": "Download", "description": "…"}],
      "integrations": [
        {"provider": "radarr", "name": "Radarr", "base_url": "http://…:7878",
         "api_key": "…", "profile_id": 9, "root_folder": "/data/media/movies",
         "tag_ids": [], "collections": ["Download"]},
        {"provider": "sonarr", …same fields…},
        {"provider": "jellyfin_sink", "name": "Jellyfin", "username": "santi",
         "metadata_provider": "tmdb"}
      ],
      "email_notifications": ["someone@example.com"]
    }

The report names each integration's id; a sink's webhook path is
`/_i/<id>`. It carries no API key."""

from __future__ import annotations

from typing import Any

from . import queries
from .client import Ryot
from .errors import InvalidArgument, UpstreamError

PROVIDERS = {"radarr": "RADARR", "sonarr": "SONARR", "jellyfin_sink": "JELLYFIN_SINK"}
SINKS = {"jellyfin_sink"}


def _require(entry: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if entry.get(k) in (None, "")]
    if missing:
        raise InvalidArgument(f"integration {entry.get('name')!r} needs {', '.join(missing)}")


async def _collections(ryot: Ryot) -> dict[str, str]:
    data = await ryot.call(queries.COLLECTIONS)
    return {c["name"].casefold(): c["id"] for c in (data.get("userCollectionsList") or {}).get("response") or []}


def _specifics(entry: dict[str, Any], collection_ids: dict[str, str]) -> dict[str, Any]:
    provider = entry["provider"]
    if provider in ("radarr", "sonarr"):
        _require(entry, "base_url", "api_key", "profile_id", "root_folder")
        names = entry.get("collections") or []
        unknown = [n for n in names if n.casefold() not in collection_ids]
        if unknown:
            raise InvalidArgument(f"integration {entry['name']!r} syncs collections that do not exist: {', '.join(unknown)}")
        return {
            f"{provider}BaseUrl": str(entry["base_url"]).rstrip("/"),
            f"{provider}ApiKey": str(entry["api_key"]),
            f"{provider}ProfileId": int(entry["profile_id"]),
            f"{provider}RootFolderPath": str(entry["root_folder"]),
            f"{provider}TagIds": [int(t) for t in entry.get("tag_ids") or []],
            f"{provider}SyncCollectionIds": [collection_ids[n.casefold()] for n in names],
        }
    specifics: dict[str, Any] = {}
    if entry.get("username"):
        specifics["jellyfinSinkUsername"] = str(entry["username"])
    if entry.get("metadata_provider"):
        specifics["jellyfinSinkMetadataProvider"] = str(entry["metadata_provider"])
    return specifics


async def apply(ryot: Ryot, spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise InvalidArgument("the spec must be a JSON object")
    me = await ryot.me()
    report: dict[str, Any] = {"user": me.name, "collections": {}, "integrations": {}, "email_notifications": {}}

    # Collections first: the pushes name them.
    existing = await _collections(ryot)
    for c in spec.get("collections") or []:
        name = str(c.get("name") or "").strip()
        if not name:
            raise InvalidArgument("every collection needs a name")
        if name.casefold() in existing:
            report["collections"][name] = "present"
            continue
        payload: dict[str, Any] = {"name": name}
        if c.get("description"):
            payload["description"] = str(c["description"])
        await ryot.call(queries.CREATE_COLLECTION, {"input": payload})
        report["collections"][name] = "created"
    collection_ids = await _collections(ryot)

    entries = spec.get("integrations") or []
    for entry in entries:
        if entry.get("provider") not in PROVIDERS:
            raise InvalidArgument(f"provider must be one of {', '.join(PROVIDERS)}")
        if not entry.get("name"):
            raise InvalidArgument("every integration needs a name")
    have = (await ryot.call(queries.USER_INTEGRATIONS)).get("userIntegrations") or []
    by_key = {(i["provider"], i.get("name")): i for i in have}
    for entry in entries:
        provider = PROVIDERS[entry["provider"]]
        current = by_key.get((provider, entry["name"]))
        payload = {
            "name": entry["name"],
            "provider": provider,
            "isDisabled": False,
            # One failed push or play is one; it must not pause the rest.
            "extraSettings": {"disableOnContinuousErrors": False},
            "providerSpecifics": _specifics(entry, collection_ids),
        }
        if current:
            payload["integrationId"] = current["id"]
        data = await ryot.call(queries.CREATE_OR_UPDATE_INTEGRATION, {"input": payload})
        if data.get("createOrUpdateUserIntegration") is not True:
            raise UpstreamError(f"Ryot did not save the {entry['name']} integration")
        report["integrations"][entry["name"]] = {"provider": entry["provider"], "state": "updated" if current else "created"}
    if entries:
        have = (await ryot.call(queries.USER_INTEGRATIONS)).get("userIntegrations") or []
        by_key = {(i["provider"], i.get("name")): i for i in have}
        for entry in entries:
            found = by_key.get((PROVIDERS[entry["provider"]], entry["name"]))
            if found is None:
                raise UpstreamError(f"the {entry['name']} integration is missing after saving it")
            report["integrations"][entry["name"]]["id"] = found["id"]
            if entry["provider"] in SINKS:
                report["integrations"][entry["name"]]["webhook_path"] = f"/_i/{found['id']}"

    emails = [str(e).strip() for e in spec.get("email_notifications") or [] if str(e).strip()]
    if emails:
        platforms = (await ryot.call(queries.NOTIFICATION_PLATFORMS)).get("userNotificationPlatforms") or []
        configured = {p["description"].casefold() for p in platforms if p.get("lot") == "EMAIL"}
        for email in emails:
            if email.casefold() in configured:
                report["email_notifications"][email] = "present"
                continue
            await ryot.call(queries.CREATE_NOTIFICATION_PLATFORM, {"input": {"lot": "EMAIL", "apiToken": email}})
            report["email_notifications"][email] = "created"
    return report
