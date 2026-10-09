"""Minimal async Jellyfin client for the shared account."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .i18n import t
from .models import Candidate
from .monitor import health

log = logging.getLogger(__name__)

ITEM_FIELDS = "Overview,Genres,ProviderIds,Tags,ProductionYear,RunTimeTicks,CommunityRating,ChildCount,RemoteTrailers"
TICKS_PER_MIN = 600_000_000


class Jellyfin:
    def __init__(self, url: str, api_key: str, user: str):
        self.url = url
        self.user = user
        self.user_id: str | None = None
        self.http = httpx.AsyncClient(
            base_url=url,
            timeout=20,
            headers={"Authorization": f'MediaBrowser Client="Corsarr", Token="{api_key}"'},
        )
        self._started_cache: tuple[float, list[str]] = (0.0, [])

    async def close(self) -> None:
        await self.http.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        try:
            r = await self.http.get(path, params={k: v for k, v in params.items() if v is not None})
            r.raise_for_status()
        except httpx.HTTPError as e:
            health.track_http("jellyfin", e)
            raise
        health.track_http("jellyfin", None)
        return r.json()

    async def uid(self) -> str:
        if self.user_id is None:
            users = await self._get("/Users")
            for u in users:
                if self.user in (u["Id"], u["Name"]) or u["Name"].lower() == self.user.lower():
                    self.user_id = u["Id"]
                    break
            else:
                health.error("jellyfin", t("check.user_not_found", user=self.user))
                raise RuntimeError(t("check.user_not_found", user=self.user))
        return self.user_id

    async def items(self, **params: Any) -> list[dict]:
        data = await self._get("/Items", userId=await self.uid(), Recursive="true",
                               EnableUserData="true", **params)
        return data.get("Items", [])

    async def item(self, item_id: str) -> dict | None:
        found = await self.items(Ids=item_id, Fields=ITEM_FIELDS + ",SeriesId,SeasonId,ParentIndexNumber,IndexNumber")
        return found[0] if found else None

    async def genres(self) -> list[str]:
        data = await self._get("/Genres", userId=await self.uid(), Recursive="true",
                               IncludeItemTypes="Movie,Series")
        return sorted({g["Name"] for g in data.get("Items", [])})

    async def started_series_ids(self) -> list[str]:
        """Series with at least one played or partly played episode, most recently watched first
        (cached 10 min)."""
        ts, cached = self._started_cache
        if time.monotonic() - ts < 600:
            return cached
        started: dict[str, None] = {}  # ordered set
        for flt in ("IsPlayed", "IsResumable"):
            for ep in await self.items(IncludeItemTypes="Episode", Filters=flt, Fields="SeriesId",
                                       SortBy="DatePlayed", SortOrder="Descending"):
                if ep.get("SeriesId"):
                    started.setdefault(ep["SeriesId"], None)
        result = list(started)
        self._started_cache = (time.monotonic(), result)
        return result

    async def unwatched_candidates(self, genres: list[str], media_types: list[str]) -> list[Candidate]:
        """Library titles in the genres that the account has not watched (series: not started)."""
        result: list[Candidate] = []
        genre_param = "|".join(genres) if genres else None
        if "movie" in media_types:
            for it in await self.items(IncludeItemTypes="Movie", Genres=genre_param,
                                       Filters="IsUnplayed", Fields=ITEM_FIELDS):
                # Partly watched movies are not "fresh" suggestions either.
                if (it.get("UserData") or {}).get("PlaybackPositionTicks", 0) > 0:
                    continue
                result.append(self.to_candidate(it))
        if "tv" in media_types:
            started = set(await self.started_series_ids())
            for it in await self.items(IncludeItemTypes="Series", Genres=genre_param, Fields=ITEM_FIELDS):
                ud = it.get("UserData") or {}
                if it["Id"] in started or ud.get("Played"):
                    continue
                result.append(self.to_candidate(it))
        return result

    def to_candidate(self, it: dict) -> Candidate:
        tmdb = (it.get("ProviderIds") or {}).get("Tmdb")
        ticks = it.get("RunTimeTicks")
        return Candidate(
            media_type="tv" if it.get("Type") == "Series" else "movie",
            source="library",
            title=it.get("Name", "?"),
            year=it.get("ProductionYear"),
            jellyfin_id=it["Id"],
            tmdb_id=int(tmdb) if tmdb and str(tmdb).isdigit() else None,
            overview=it.get("Overview") or "",
            runtime_min=round(ticks / TICKS_PER_MIN) if ticks else None,
            rating=it.get("CommunityRating"),
            genres=it.get("Genres") or [],
            keywords=[t.lower() for t in it.get("Tags") or []],
            seasons=it.get("ChildCount") if it.get("Type") == "Series" else None,
            trailer_url=next((t["Url"] for t in it.get("RemoteTrailers") or [] if t.get("Url")), None),
        )

    async def taste_items(self) -> list[dict]:
        """Watched movies, started series and favourites – input for the taste profile."""
        fields = "Genres,Tags,ProviderIds,ProductionYear"
        played = await self.items(IncludeItemTypes="Movie", Filters="IsPlayed", Fields=fields,
                                  SortBy="DatePlayed", SortOrder="Descending")
        favs = await self.items(IncludeItemTypes="Movie,Series", Filters="IsFavorite", Fields=fields)
        ids = await self.started_series_ids()
        series: list[dict] = []
        for i in range(0, len(ids), 50):
            series += await self.items(Ids=",".join(ids[i:i + 50]), Fields=fields)
        order = {sid: n for n, sid in enumerate(ids)}
        series.sort(key=lambda it: order.get(it["Id"], len(order)))  # keep "last watched first"
        for it in favs:
            it["_favorite"] = True
        return played + series + favs

    async def season_episodes(self, series_id: str, season_id: str) -> list[dict]:
        data = await self._get(f"/Shows/{series_id}/Episodes", userId=await self.uid(),
                               seasonId=season_id, Fields="UserData")
        return data.get("Items", [])

    async def series_finished(self, series_id: str) -> bool:
        items = await self.items(Ids=series_id)
        return bool(items and (items[0].get("UserData") or {}).get("Played"))

    async def poster(self, item_id: str) -> bytes | None:
        try:
            r = await self.http.get(f"/Items/{item_id}/Images/Primary", params={"maxWidth": 500})
            if r.status_code == 200 and r.content:
                return r.content
        except httpx.HTTPError as e:
            log.warning(t("log.poster_failed", item=item_id, error=e))
        return None
