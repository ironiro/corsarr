"""Async Jellyseerr client: TMDB discovery, details and requests."""
from __future__ import annotations

import asyncio
import re
import logging
from typing import Any

import httpx

from .i18n import language, t
from .models import Candidate, FilmCollection
from .monitor import health

log = logging.getLogger(__name__)

POSTER_BASE = "https://image.tmdb.org/t/p/w500"
LOGO_BASE = "https://image.tmdb.org/t/p/w92"
MIN_VOTES = 200  # TMDB votes – keeps out obscure titles and ones released last week


def subscribed(watch_providers: list[dict] | None, region: str, provider_ids: set[int]) -> list[str]:
    """Names of the subscribed services that include a title in `region`.

    `watch_providers` is the `watchProviders` field of Seerr's movie/tv details: one entry per country
    (iso_3166_1) with `flatrate` (in the subscription) and `buy` lists. Only flatrate counts – renting or
    buying is no reason to skip the request."""
    if not region or not provider_ids:
        return []
    entry = next((w for w in watch_providers or [] if w.get("iso_3166_1") == region), None)
    names: list[str] = []
    for p in (entry or {}).get("flatrate") or []:
        if p.get("id") in provider_ids and p.get("name") and p["name"] not in names:
            names.append(p["name"])
    return names


class Jellyseerr:
    def __init__(self, url: str, api_key: str, track: bool = True):
        self.http = httpx.AsyncClient(base_url=f"{url}/api/v1", timeout=20,
                                      headers={"X-Api-Key": api_key})
        self.track = track  # False for the GUI's pickers, which may try addresses that were only typed in
        # Streaming services of the household (STREAMING_REGION / STREAMING_PROVIDERS); set from the
        # configuration and replaced when it is saved, without restarting the bot. Empty = off.
        self.streaming_region = ""
        self.streaming_ids: set[int] = set()

    def set_streaming(self, region: str, provider_ids: set[int]) -> None:
        self.streaming_region, self.streaming_ids = region, set(provider_ids)

    async def close(self) -> None:
        await self.http.aclose()

    async def _get(self, path: str, localized: bool = True, **params: Any) -> Any:
        if localized:
            params = {"language": language(), **params}  # TMDB texts in the language of the current request
        try:
            r = await self.http.get(path, params=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            if self.track:
                health.track_http("jellyseerr", e)
            raise
        if self.track:
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
            if c.source == "new":  # what is in the library or on its way needs no streaming service
                c.streaming = subscribed(d.get("watchProviders"), self.streaming_region, self.streaming_ids)

        await asyncio.gather(*(one(c) for c in cands))

    async def watch_regions(self) -> list[dict]:
        """[{code, name}] of the countries TMDB has streaming data for, sorted by name."""
        # The watch provider routes accept no "language" (Seerr's API spec rejects unknown parameters with 400)
        data = await self._get("/watchproviders/regions", localized=False)
        regions = [{"code": r["iso_3166_1"], "name": r.get("native_name") or r.get("english_name") or r["iso_3166_1"]}
                   for r in data if r.get("iso_3166_1")]
        return sorted(regions, key=lambda r: r["name"].casefold())

    async def watch_providers(self, region: str) -> list[dict]:
        """[{id, name, logo}] of the streaming services in a country – movies and series together,
        the most popular first (TMDB's display priority)."""
        movies, tv = await asyncio.gather(self._get("/watchproviders/movies", localized=False, watchRegion=region),
                                          self._get("/watchproviders/tv", localized=False, watchRegion=region))
        found: dict[int, dict] = {}
        for p in [*movies, *tv]:
            if p.get("id") is None or not p.get("name"):
                continue
            prio = p.get("displayPriority") if p.get("displayPriority") is not None else 999
            logo = f"{LOGO_BASE}{p['logoPath']}" if p.get("logoPath") else None
            seen = found.get(p["id"])
            if seen is None or prio < seen["prio"]:
                found[p["id"]] = {"id": p["id"], "name": p["name"], "prio": prio, "logo": logo or (seen or {}).get("logo")}
            elif not seen["logo"]:
                seen["logo"] = logo
        ordered = sorted(found.values(), key=lambda p: (p["prio"], p["name"].casefold()))
        return [{k: v for k, v in p.items() if k != "prio"} for p in ordered]

    @staticmethod
    def match_providers(names: list[str], providers: list[dict]) -> tuple[list[dict], list[str]]:
        """Streaming services named in the chat ("Prime", "Disney+") -> entries of `providers` (most popular
        first, so "Netflix" means Netflix, not "Netflix basic with Ads"). Returns (matched, not found)."""
        def norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]", "", s.lower().replace("+", "plus"))
        matched, missing = [], []
        for name in names:
            q = norm(name)
            hit = next((p for p in providers if norm(p["name"]) == q), None) if q else None
            hit = hit or next((p for p in providers if q and q in norm(p["name"])), None)
            if hit and hit not in matched:
                matched.append(hit)
            elif not hit:
                missing.append(name)
        return matched, missing

    async def details(self, media_type: str, tmdb_id: int) -> dict:
        return await self._get(f"/{media_type}/{tmdb_id}")

    async def collection(self, collection_id: int) -> FilmCollection:
        """A TMDB collection with what Jellyseerr knows about each part (GET /collection/{id}: parts are
        movie results sorted by release date, each with its mediaInfo)."""
        data = await self._get(f"/collection/{collection_id}")
        parts = []
        for res in data.get("parts", []):
            cand = self._from_result(res, "movie")
            status = (res.get("mediaInfo") or {}).get("status", 1)
            # 4/5 = (partly) available, 2/3 = requested/processing, 6 = blocklisted; 1 unknown, 7 deleted = missing
            cand.source = ("library" if status in (4, 5) else "pending" if status in (2, 3)
                           else "blocked" if status == 6 else "new")
            cand.votes = res.get("voteCount")
            parts.append(cand)
        poster = data.get("posterPath")
        return FilmCollection(tmdb_id=int(data.get("id") or collection_id), name=data.get("name") or "?",
                              poster_url=f"{POSTER_BASE}{poster}" if poster else None, parts=parts)

    async def request(self, media_type: str, tmdb_id: int) -> dict:
        body: dict[str, Any] = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            d = await self.details("tv", tmdb_id)
            body["seasons"] = [s["seasonNumber"] for s in d.get("seasons", []) if s.get("seasonNumber", 0) > 0]
        try:
            r = await self.http.post("/request", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            if self.track:
                health.track_http("jellyseerr", e)
            raise
        if self.track:
            health.track_http("jellyseerr", None)
        return r.json()

    async def keywords_for(self, media_type: str, tmdb_id: int) -> tuple[list[str], list[str]]:
        """(genres, keywords) of a title – used to attach feedback to concrete tags."""
        d = await self.details(media_type, tmdb_id)
        kw = d.get("keywords") or []
        if isinstance(kw, dict):
            kw = kw.get("keywords") or kw.get("results") or []
        return [g["name"] for g in d.get("genres", [])], [k["name"].lower() for k in kw if "name" in k]
