"""After-watching feedback: webhook events, scheduling and storing ratings/traits."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Awaitable, Callable

from .db import DB, now, parse_iso
from .i18n import t
from .jellyfin import Jellyfin
from .jellyseerr import Jellyseerr
from .models import title_key
from .profile import ProfileBuilder

log = logging.getLogger(__name__)

FINISHED_PCT = 0.8          # stopped later than this counts as "watched to the end"
MIN_WATCH_TICKS = 5 * 60 * 10_000_000  # ignore accidental starts under 5 minutes
ABORT_UNANSWERED_WEIGHT = 0.3  # an ignored abort question is a light thumbs down
ABORT_ANSWER_WINDOW = timedelta(days=2)
QUESTION_EXPIRY = timedelta(days=7)
OTHERS_WINDOW = timedelta(days=1)  # once someone rated: how long the others still have

RATING_VALUE = {"up": 1, "meh": 0, "down": -1}

# notifier(request) -> True when the question was posted
Notifier = Callable[[dict], Awaitable[bool]]


class FeedbackService:
    def __init__(self, db: DB, jellyfin: Jellyfin, seerr: Jellyseerr, profiles: ProfileBuilder):
        self.db, self.jellyfin, self.seerr, self.profiles = db, jellyfin, seerr, profiles
        self.notifier: Notifier | None = None

    # --- webhook ----------------------------------------------------------
    async def on_playback_stop(self, event: dict) -> None:
        item_id = event.get("itemId") or event.get("ItemId")
        if not item_id:
            return
        item = await self.jellyfin.item(item_id)
        if not item:
            log.info(t("log.unknown_item", item=item_id))
            return
        if item.get("Type") == "Movie":
            await self._movie_stopped(item, event)
        elif item.get("Type") == "Episode" and item.get("SeriesId"):
            await self._episode_stopped(item, event)

    @staticmethod
    def _progress(item: dict, event: dict) -> tuple[float, bool, int]:
        ud = item.get("UserData") or {}
        runtime = item.get("RunTimeTicks") or 0
        pos = ud.get("PlaybackPositionTicks") or _int(event.get("positionTicks"))
        completed = bool(ud.get("Played")) or str(event.get("playedToCompletion")).lower() == "true"
        pct = 1.0 if completed else (pos / runtime if runtime else 0.0)
        return pct, completed or pct >= FINISHED_PCT, pos

    async def _movie_stopped(self, item: dict, event: dict) -> None:
        pct, finished, pos = self._progress(item, event)
        tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
        key = title_key("movie", tmdb, item["Id"])
        title = _label(item)
        if not finished and pos < MIN_WATCH_TICKS:
            return
        self.db.touch_watch_state(item["Id"], key, title, "movie", pct, finished)
        open_reqs = self.db.open_requests_for(item["Id"], ("movie", "abort"))
        if finished:
            for r in open_reqs:
                if r["kind"] == "abort" and r["status"] in ("scheduled", "pending"):
                    self.db.update_request(r["id"], status="cancelled")
            if not any(r["kind"] == "movie" for r in open_reqs) and key not in self.db.feedback_keys():
                req_id = self.db.add_request("movie", key, item["Id"], title, "movie", now(), status="pending")
                await self._send(req_id)
            return
        # Stopped early: ask after N days without continuing; a later stop pushes the date.
        due = now() + timedelta(days=self.db.settings()["abort_days"])
        for r in open_reqs:
            if r["kind"] == "abort" and r["status"] in ("scheduled", "pending"):
                self.db.update_request(r["id"], due_at=due, status="scheduled", extra={"pct": pct})
                return
        self.db.add_request("abort", key, item["Id"], title, "movie", due, extra={"pct": pct})

    async def _episode_stopped(self, item: dict, event: dict) -> None:
        series_id = item["SeriesId"]
        series = await self.jellyfin.item(series_id)
        if not series:
            return
        tmdb = (series.get("ProviderIds") or {}).get("Tmdb")
        key = title_key("tv", tmdb, series_id)
        title = _label(series)
        pct, finished, _ = self._progress(item, event)
        series_done = bool((series.get("UserData") or {}).get("Played"))
        self.db.touch_watch_state(series_id, key, title, "tv", pct, series_done)

        # Resumed watching: a scheduled pause question is obsolete.
        for r in self.db.open_requests_for(series_id, ("series_pause",)):
            if r["status"] in ("scheduled", "pending"):
                self.db.update_request(r["id"], status="cancelled")

        if not finished or not item.get("SeasonId"):
            return
        # Season end is derived from Jellyfin's played state of all episodes of the season,
        # so it does not depend on what the webhook plugin reports.
        episodes = await self.jellyfin.season_episodes(series_id, item["SeasonId"])
        unplayed = [e for e in episodes if not (e.get("UserData") or {}).get("Played")
                    and e.get("Id") != item["Id"]]
        if unplayed:
            return
        season = item.get("ParentIndexNumber")
        for r in self.db.open_requests_for(series_id, ("season",)):
            if r["extra"].get("season") == season:
                return
        req_id = self.db.add_request("season", key, series_id, title, "tv", now(), status="pending",
                                     extra={"season": season, "series_done": series_done})
        await self._send(req_id)

    # --- periodic job -------------------------------------------------------
    async def tick(self) -> None:
        settings = self.db.settings()
        for req in self.db.due_requests():
            if req["kind"] == "abort" and req["status"] == "scheduled":
                item = await self.jellyfin.item(req["jellyfin_id"])
                if item and (item.get("UserData") or {}).get("Played"):
                    # Finished in the meantime without a webhook we saw – ask normally instead.
                    self.db.update_request(req["id"], kind="movie", status="pending")
                else:
                    self.db.update_request(req["id"], status="pending")
            await self._send(req["id"])

        pause = timedelta(days=settings["series_pause_days"])
        for st in self.db.series_states():
            if now() - parse_iso(st["last_activity"]) < pause:
                continue
            if self.db.pause_asked(st["jellyfin_id"], st["last_activity"]):
                continue
            if await self.jellyfin.series_finished(st["jellyfin_id"]):
                self.db.touch_watch_state(st["jellyfin_id"], st["title_key"], st["title"], "tv", 1.0, True)
                continue
            req_id = self.db.add_request("series_pause", st["title_key"], st["jellyfin_id"], st["title"],
                                         "tv", now(), status="pending",
                                         extra={"activity": st["last_activity"]})
            await self._send(req_id)

        for req in self.db.sent_requests():
            age = now() - parse_iso(req["sent_at"])
            if req["kind"] == "abort" and age > ABORT_ANSWER_WINDOW:
                await self.store_rating(req, "down", weight=ABORT_UNANSWERED_WEIGHT)
                self.db.update_request(req["id"], status="expired")
                log.info(t("log.abort_expired", title=req["title"]))
            elif req["extra"].get("ratings") and age > OTHERS_WINDOW:
                self.db.update_request(req["id"], status="answered")  # the others didn't rate – fine
            elif age > QUESTION_EXPIRY:
                self.db.update_request(req["id"], status="expired")

    async def _send(self, req_id: int) -> None:
        req = self.db.request(req_id)
        if not req or req["status"] != "pending" or self.notifier is None:
            return
        try:
            await self.notifier(req)  # stays 'pending' on failure, the job retries
        except Exception:
            log.exception(t("log.question_not_sent", id=req_id))

    # --- answers ------------------------------------------------------------
    async def title_tags(self, req: dict) -> tuple[list[str], list[str]]:
        key = req["title_key"]
        if not key.startswith("jf:"):
            media_type, tmdb_id = key.split(":")
            try:
                return await self.seerr.keywords_for(media_type, int(tmdb_id))
            except Exception as e:
                log.warning(t("log.tmdb_keywords_failed", title=req["title"], error=e))
        item = await self.jellyfin.item(req["jellyfin_id"])
        if not item:
            return [], []
        return item.get("Genres") or [], [t.lower() for t in item.get("Tags") or []]

    async def store_rating(self, req: dict, rating: str, weight: float = 1.0,
                           free_text: str | None = None, rater: tuple[int, str] | None = None) -> int:
        """Store a rating. With a rater (Telegram user id, first name) each person has one rating per
        question – rating again replaces their earlier one; the request's extra keeps who rated what."""
        genres, keywords = await self.title_tags(req)
        ratings = dict(req["extra"].get("ratings") or {})
        previous = ratings.get(str(rater[0])) if rater else None
        if previous and previous.get("feedback_id"):
            self.db.delete_feedback(previous["feedback_id"])
        fb_id = self.db.add_feedback(req["title_key"], req["title"], req["media_type"],
                                     RATING_VALUE[rating], weight, free_text, genres, keywords, rater)
        if rater:
            ratings[str(rater[0])] = {"name": rater[1], "rating": rating, "feedback_id": fb_id}
        extra = {**req["extra"], "feedback_id": fb_id, "ratings": ratings}
        self.db.update_request(req["id"], extra=extra)
        req["extra"] = extra
        self.profiles.invalidate()
        return fb_id


def _label(item: dict) -> str:
    year = item.get("ProductionYear")
    return f"{item.get('Name', '?')} ({year})" if year else item.get("Name", "?")


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
