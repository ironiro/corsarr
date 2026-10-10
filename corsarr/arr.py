"""Download notifications from Sonarr and Radarr, bundled so a backfill doesn't flood the group.

Every imported file is stored first. A periodic job then turns each series/movie into one message once
nothing new arrived for a while: fresh episodes (aired in the last days) after a short pause, backfills
(old seasons) only when the whole download has settled – "88 episodes from seasons 1–4" instead of 88
messages. Stored imports survive a restart of the bot.

Sonarr and Radarr don't retry a webhook that failed (e.g. Corsarr was restarting). With their access
remembered, catch_up() reads their history and records imports whose webhook never arrived.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from .db import DB, iso, now, parse_iso
from .http import arr_api, arr_headers, borrowed_client
from .i18n import t
from .models import label

log = logging.getLogger(__name__)

FRESH_AGE = timedelta(days=7)          # aired within this window = a "new episode" of a running show
QUIET_FRESH = timedelta(minutes=2)     # e.g. double episodes arrive together
QUIET_BACKFILL = timedelta(minutes=15)  # season packs import file by file over several minutes
QUIET_MOVIE = timedelta(minutes=1)
LIST_EPISODES = 3                      # up to this many fresh episodes are named individually
CATCH_UP_FIRST = timedelta(hours=48)   # first catch-up: how far back to look
CATCH_UP_OVERLAP = timedelta(minutes=10)  # later runs re-read a little, imports may be logged late
KNOWN_FOR = timedelta(days=14)         # an import recorded within this window counts as already known


def _aired(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_iso(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def record_sonarr(db: DB, event: dict) -> int:
    """Store the episodes of a Sonarr 'Download' event. Returns how many were stored."""
    if event.get("eventType") != "Download" or event.get("isUpgrade"):
        return 0
    series = event.get("series") or {}
    stored = 0
    for ep in event.get("episodes") or []:
        aired = _aired(ep.get("airDateUtc"))
        fresh = aired is not None and now() - aired <= FRESH_AGE
        db.add_import("episode", f"sonarr:{series.get('id')}", _label(series), series.get("tmdbId"),
                      ep.get("seasonNumber"), ep.get("episodeNumber"), ep.get("title"), fresh)
        stored += 1
    return stored


def record_radarr(db: DB, event: dict) -> int:
    if event.get("eventType") != "Download" or event.get("isUpgrade"):
        return 0
    movie = event.get("movie") or {}
    db.add_import("movie", f"radarr:{movie.get('id')}", _label(movie), movie.get("tmdbId"), None, None, None,
                  True)
    return 1


async def catch_up(db: DB, kind: str, url: str, api_key: str, client: httpx.AsyncClient | None = None) -> int:
    """Record imports from Sonarr/Radarr's history that no webhook reported. Returns how many were added.

    The first run looks back CATCH_UP_FIRST; later runs continue where the last one stopped. Imports already
    recorded (by webhook or an earlier run) are skipped, and so are quality upgrades, like in the webhook.
    """
    cursor_key = f"arr_cursor:{kind}"
    started = now()
    cursor = db.get_state(cursor_key)
    since = parse_iso(cursor) - CATCH_UP_OVERLAP if cursor else started - CATCH_UP_FIRST
    include = {"includeSeries": "true", "includeEpisode": "true"} if kind == "sonarr" else {"includeMovie": "true"}
    async with borrowed_client(client, timeout=30) as c:
        r = await c.get(f"{arr_api(url)}/history/since", headers=arr_headers(api_key),
                        params={"date": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **include})
        r.raise_for_status()
        records = r.json()

    # An upgrade deletes the old file with reason "Upgrade" – the webhook marks those as isUpgrade.
    item = "episodeId" if kind == "sonarr" else "movieId"
    upgraded = {rec.get(item) for rec in records
                if rec.get("eventType") in ("episodeFileDeleted", "movieFileDeleted")
                and (rec.get("data") or {}).get("reason") == "Upgrade"}
    added = 0
    for rec in records:
        if rec.get("eventType") != "downloadFolderImported" or rec.get(item) in upgraded:
            continue
        if kind == "sonarr":
            series, ep = rec.get("series") or {}, rec.get("episode") or {}
            if not series or not ep or db.has_import(f"sonarr:{series.get('id')}", ep.get("seasonNumber"),
                                                     ep.get("episodeNumber"), now() - KNOWN_FOR):
                continue
            added += record_sonarr(db, {"eventType": "Download", "series": series, "episodes": [ep]})
        else:
            movie = rec.get("movie") or {}
            if not movie or db.has_import(f"radarr:{movie.get('id')}", None, None, now() - KNOWN_FOR):
                continue
            added += record_radarr(db, {"eventType": "Download", "movie": movie})
    db.set_state(cursor_key, iso(started))
    return added


def due_messages(db: DB) -> list[tuple[list[int], str]]:
    """Messages for every series/movie whose imports have settled: [(import ids, text)]."""
    groups: dict[str, list[dict]] = {}
    for row in db.pending_imports():
        groups.setdefault(row["group_key"], []).append(row)
    result = []
    for rows in groups.values():
        ids = [r["id"] for r in rows]
        try:
            last = max(parse_iso(r["created_at"]) for r in rows)
            if rows[0]["kind"] == "movie":
                quiet = QUIET_MOVIE
            else:
                quiet = QUIET_FRESH if all(r["fresh"] for r in rows) else QUIET_BACKFILL
            if now() - last >= quiet:
                result.append((ids, _message(db, rows)))
        except Exception:  # one odd row (e.g. broken fields) must not hold up the other notifications
            log.exception(t("log.notify_build_failed", title=rows[0].get("title")))
            db.mark_imports_notified(ids)
    return result


def _message(db: DB, rows: list[dict]) -> str:
    first = rows[0]
    title = first["title"]
    requested = db.was_requested("movie" if first["kind"] == "movie" else "tv", first["tmdb_id"])
    suffix = t("notify.requested") if requested else ""
    if first["kind"] == "movie":
        return t("notify.movie", title=title) + suffix

    episodes = sorted({(r["season"] or 0, r["episode"] or 0): r for r in rows}.values(),
                      key=lambda r: (r["season"] or 0, r["episode"] or 0))
    if all(r["fresh"] for r in episodes) and len(episodes) <= LIST_EPISODES:
        names = [f"S{r['season'] or 0:02d}E{r['episode'] or 0:02d}"
                 + (" " + t("notify.episode_title", title=r["episode_title"]) if r["episode_title"] else "")
                 for r in episodes]
        return t("notify.episodes", series=title, episodes=", ".join(names)) + suffix
    seasons = sorted({r["season"] for r in episodes if r["season"] is not None})
    label = t("notify.season_one") if len(seasons) == 1 else t("notify.season_many")
    return t("notify.bulk", series=title, n=len(episodes), seasons=f"{label} {_ranges(seasons)}") + suffix


def _ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 5] -> '1–3, 5'"""
    parts: list[str] = []
    start = prev = None
    for n in numbers + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            parts.append(f"{start}–{prev}" if prev != start else str(start))
            start = prev = n
    return ", ".join(parts)


def _label(item: dict) -> str:
    """Title and year of a Sonarr series / Radarr movie object."""
    return label(item.get("title"), item.get("year"))
