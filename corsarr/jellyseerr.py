"""Async Jellyseerr client: TMDB discovery, details and requests."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .i18n import language, t
from .models import Candidate
from .monitor import health

log = logging.getLogger(__name__)

POSTER_BASE = "https://image.tmdb.org/t/p/w500"
MIN_VOTES = 200  # TMDB votes – keeps out obscure titles and ones released last week


class Jellyseerr:
    def __init__(self, url: str, api_key: str):
        self.http = httpx.AsyncClient(base_url=f"{url}/api/v1", timeout=20,
                                      headers={"X-Api-Key": api_key})

    async def close(self) -> None:
        await self.http.aclose()

    async def _get(self, path: str, localized: bool = True, **params: Any) -> Any:
        if localized:
            params = {"language": language(), **params}  # TMDB texts in the language of the current request
        try:
            r = await self.http.get(path, params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            health.track_http("jellyseerr", e)
            raise
        health.track_http("jellyseerr", None)
        return r.json()

    async def genres(self, media_type: str) -> list[dict]:
        """[{id, name}] for 'movie' or 'tv'."""
        return await self._get(f"/genres/{media_type}")

    async def discover(self, media_type: str, genre_ids: list[int], exclude: set[str],
                       want: int = 15, max_pages: int = 3) -> list[Candidate]:
        path = "/discover/movies" if media_type == "movie" else "/discover/tv"
        found: list[Candidate] = []
        for page in range(1, max_pages + 1):
            params: dict[str, Any] = {"page": page, "voteCountGte": MIN_VOTES}
            if genre_ids:
                params["genre"] = "|".join(str(g) for g in genre_ids)  # '|' = OR at TMDB
            # No 'language' here: on discover Jellyseerr treats it as the *original* language filter
            # (language=de -> only German-made films). Titles still come localized via Jellyseerr's locale.
            data = await self._get(path, localized=False, **params)
            for res in data.get("results", []):
                info = res.get("mediaInfo") or {}
                if info.get("status", 1) >= 2:  # already requested, processing or available
                    continue
                cand = self._from_result(res, media_type)
                if cand.key in exclude or not cand.overview:
                    continue
                found.append(cand)
            if len(found) >= want or page >= data.get("totalPages", 1):
                break
        return found[:want]

    async def search(self, query: str) -> list[Candidate]:
        """Movies and series for a search term (TMDB search), with what Jellyseerr knows about each:
        in the library, already requested, or new. People and collections are left out."""
        data = await self._get("/search", query=query, page=1)
        found = []
        for res in data.get("results", []):
            if res.get("mediaType") not in ("movie", "tv"):
                continue
            cand = self._from_result(res, res["mediaType"])
            status = (res.get("mediaInfo") or {}).get("status", 1)
            # 4 = partly available (e.g. some seasons), 5 = available; 2/3 = requested/processing
            cand.source = "library" if status in (4, 5) else "pending" if status in (2, 3) else "new"
            cand.votes = res.get("voteCount")
            found.append(cand)
        return found

    def _from_result(self, res: dict, media_type: str) -> Candidate:
        date = res.get("releaseDate") or res.get("firstAirDate") or ""
        return Candidate(
            media_type=media_type,
            source="new",
            title=res.get("title") or res.get("name") or "?",
            year=int(date[:4]) if date[:4].isdigit() else None,
            tmdb_id=int(res["id"]),
            overview=res.get("overview") or "",
            rating=res.get("voteAverage"),
            poster_url=f"{POSTER_BASE}{res['posterPath']}" if res.get("posterPath") else None,
        )

    async def enrich(self, cands: list[Candidate]) -> None:
        """Fill runtime, genres, keywords and season count from the detail endpoints."""
        async def one(c: Candidate) -> None:
            try:
                d = await self.details(c.media_type, c.tmdb_id)
            except httpx.HTTPError as e:
                log.warning(t("log.details_failed", title=c.label, error=e))
                return
            c.genres = [g["name"] for g in d.get("genres", [])]
            kw = d.get("keywords") or []
            if isinstance(kw, dict):  # tolerate the raw TMDB shape
                kw = kw.get("keywords") or kw.get("results") or []
            c.keywords = [k["name"].lower() for k in kw if "name" in k]
            if c.media_type == "movie":
                c.runtime_min = d.get("runtime") or None
            else:
                ert = d.get("episodeRunTime") or []
                c.runtime_min = ert[0] if ert else None
                c.seasons = d.get("numberOfSeasons")
            if c.rating is None:
                c.rating = d.get("voteAverage")
            videos = d.get("relatedVideos") or []
            trailer = next((v for v in videos if v.get("type") == "Trailer" and v.get("url")), None)
            c.trailer_url = (trailer or {}).get("url")

        await asyncio.gather(*(one(c) for c in cands))

    async def details(self, media_type: str, tmdb_id: int) -> dict:
        return await self._get(f"/{media_type}/{tmdb_id}")

    async def request(self, media_type: str, tmdb_id: int) -> dict:
        body: dict[str, Any] = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            d = await self.details("tv", tmdb_id)
            body["seasons"] = [s["seasonNumber"] for s in d.get("seasons", []) if s.get("seasonNumber", 0) > 0]
        try:
            r = await self.http.post("/request", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            health.track_http("jellyseerr", e)
            raise
        health.track_http("jellyseerr", None)
        return r.json()

    async def keywords_for(self, media_type: str, tmdb_id: int) -> tuple[list[str], list[str]]:
        """(genres, keywords) of a title – used to attach feedback to concrete tags."""
        d = await self.details(media_type, tmdb_id)
        kw = d.get("keywords") or []
        if isinstance(kw, dict):
            kw = kw.get("keywords") or kw.get("results") or []
        return [g["name"] for g in d.get("genres", [])], [k["name"].lower() for k in kw if "name" in k]
