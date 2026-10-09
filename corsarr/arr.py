"""Download notifications from Sonarr and Radarr, bundled so a backfill doesn't flood the group.

Every imported file is stored first. A periodic job then turns each series/movie into one message once
nothing new arrived for a while: fresh episodes (aired in the last days) after a short pause, backfills
(old seasons) only when the whole download has settled – "88 episodes from seasons 1–4" instead of 88
messages. Stored imports survive a restart of the bot.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .db import DB, now, parse_iso
from .i18n import t

FRESH_AGE = timedelta(days=7)          # aired within this window = a "new episode" of a running show
QUIET_FRESH = timedelta(minutes=2)     # e.g. double episodes arrive together
QUIET_BACKFILL = timedelta(minutes=15)  # season packs import file by file over several minutes
QUIET_MOVIE = timedelta(minutes=1)
LIST_EPISODES = 3                      # up to this many fresh episodes are named individually


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


def due_messages(db: DB) -> list[tuple[list[int], str]]:
    """Messages for every series/movie whose imports have settled: [(import ids, text)]."""
    groups: dict[str, list[dict]] = {}
    for row in db.pending_imports():
        groups.setdefault(row["group_key"], []).append(row)
    result = []
    for rows in groups.values():
        last = max(parse_iso(r["created_at"]) for r in rows)
        if rows[0]["kind"] == "movie":
            quiet = QUIET_MOVIE
        else:
            quiet = QUIET_FRESH if all(r["fresh"] for r in rows) else QUIET_BACKFILL
        if now() - last >= quiet:
            result.append(([r["id"] for r in rows], _message(db, rows)))
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
        names = [f"S{r['season']:02d}E{r['episode']:02d}"
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
    year = item.get("year")
    title = item.get("title") or "?"
    return f"{title} ({year})" if year else title
