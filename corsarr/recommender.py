"""5–6 suggestions per request: at most 2 from the library (shown first), the rest new via Jellyseerr."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from .db import DB
from .i18n import t
from .jellyfin import Jellyfin
from .jellyseerr import POSTER_BASE, Jellyseerr
from .llm import LLM, Understanding
from .models import Candidate, FilmCollection
from .profile import ProfileBuilder

log = logging.getLogger(__name__)

PICKS_MIN, PICKS_MAX = 5, 6
MAX_LIBRARY_PICKS = 2  # library titles come first, but only a taste of them – the rest is new
LIBRARY_POOL = 8       # candidates the model sees per source
NEW_POOL = 20
LOOKUP_QUERIES = 4     # search terms per lookup
LOOKUP_POOL = 15       # search hits the model sees
LOOKUP_PICKS = 3

def genre_match(c: Candidate, wanted: set[str]) -> int:
    """How many of the requested genres the title carries (names compared case-insensitively)."""
    return sum(1 for g in c.genres if g.lower() in wanted)


@dataclass
class Recommendation:
    intro: str
    picks: list[tuple[Candidate, str]]
    library_count: int
    collection: FilmCollection | None = None  # set when they asked for all parts of a film series


class Recommender:
    def __init__(self, db: DB, jellyfin: Jellyfin, seerr: Jellyseerr, llm: LLM, profiles: ProfileBuilder):
        self.db, self.jellyfin, self.seerr, self.llm, self.profiles = db, jellyfin, seerr, llm, profiles

    async def recommend(self, request: str, und: Understanding, speaker: str) -> Recommendation:
        new_only = und.intent == "new_only"
        media_types = list(und.media_types) or ["movie", "tv"]
        exclude = self.db.rejected_keys() | self.db.feedback_keys() | self.db.accepted_keys()
        recent = self.db.recently_suggested(days=3)
        profile = await self.profiles.get()
        genres = self.llm.requested_genres(und)
        wanted = {g.lower() for g in genres}

        def rank(c: Candidate) -> tuple[int, float]:
            # More requested genres first ("horror or thriller": horror-thrillers before plain thrillers).
            return genre_match(c, wanted), c.score

        library: list[Candidate] = []
        if not new_only:
            library = [c for c in await self.jellyfin.unwatched_candidates(und.jellyfin_genres, media_types)
                       if c.key not in exclude]
            for c in library:
                c.score = profile.score(c) - (1.0 if c.key in recent else 0.0)
            library.sort(key=rank, reverse=True)
        new = await self._new_titles(und, media_types, exclude | {c.key for c in library}, recent)
        new.sort(key=rank, reverse=True)

        lib_pool, new_pool = library[:LIBRARY_POOL], new[:NEW_POOL]
        candidates = lib_pool + new_pool
        if not candidates:
            return Recommendation(intro="", picks=[], library_count=0)

        max_lib = 0 if new_only else MAX_LIBRARY_PICKS
        if not new_only and len(new_pool) < PICKS_MIN - max_lib:
            max_lib = PICKS_MAX - len(new_pool)  # Jellyseerr found too little: let the library fill in
        notes = t("sit.notes_new_only") if new_only else t("sit.notes_mix", max_lib=max_lib)
        n_max = min(PICKS_MAX, len(candidates))
        n_min = min(PICKS_MIN, n_max)
        taste = {"liked": profile.liked, "disliked": profile.disliked,
                 "recent_movies": profile.recent_movies, "recent_series": profile.recent_series}
        sel = await self.llm.select(request, candidates, speaker, n_min, n_max, notes, genres, taste)

        def fits(c: Candidate) -> bool:
            # Only titles whose metadata carries a requested genre may be added without the model.
            return not wanted or genre_match(c, wanted) > 0

        picks: list[tuple[Candidate, str]] = []
        seen: set[int] = set()

        def take(i: int, reason: str) -> None:
            c = candidates[i]
            lib_taken = sum(1 for pc, _ in picks if pc.source == "library")
            if i in seen or len(picks) >= n_max or (c.source == "library" and lib_taken >= max_lib):
                return
            seen.add(i)
            picks.append((c, reason))

        for p in sel.picks:
            if 0 <= p.id < len(candidates):
                take(p.id, p.reason)
        # Top up if the model picked too few: new titles first, then the library within its cap.
        order = [i for i, c in enumerate(candidates) if c.source == "new"] + \
                [i for i, c in enumerate(candidates) if c.source == "library"]
        for i in order:
            if len(picks) >= n_min:
                break
            if fits(candidates[i]):
                take(i, "")
        picks.sort(key=lambda pc: pc[0].source != "library")  # library first
        return Recommendation(intro=sel.intro, picks=picks, library_count=len(library))

    async def lookup(self, request: str, und: Understanding, speaker: str) -> Recommendation:
        """A specific title they asked about ("there's a new Marvel series with Vision"): search TMDB via
        Jellyseerr with the model's search terms, then let the model say which hits are meant."""
        queries = list(dict.fromkeys(q.strip() for q in und.search_queries if q.strip()))[:LOOKUP_QUERIES]
        queries = queries or [request]
        results = await asyncio.gather(*(self.seerr.search(q) for q in queries), return_exceptions=True)
        hits: list[Candidate] = []
        seen: set[str] = set()
        for query, res in zip(queries, results):
            if isinstance(res, Exception):
                log.warning(t("log.search_failed", query=query, error=res))
                continue
            for c in res:
                if (not und.media_types or c.media_type in und.media_types) and c.key not in seen:
                    seen.add(c.key)
                    hits.append(c)
        log.info(t("log.lookup", queries=" | ".join(queries), n=len(hits)))
        if not hits:
            return Recommendation(intro="", picks=[], library_count=0)
        hits = hits[:LOOKUP_POOL]
        sel = await self.llm.identify(request, hits, speaker)
        picks: list[tuple[Candidate, str]] = []
        for p in sel.picks:
            if 0 <= p.id < len(hits) and all(hits[p.id] is not c for c, _ in picks):
                picks.append((hits[p.id], p.reason))
        picks = picks[:LOOKUP_PICKS]
        if und.whole_collection and picks:
            coll = await self._collection([c for c, _ in picks])
            if coll is not None:
                return Recommendation(intro=await self._collection_intro(request, coll, speaker), picks=picks,
                                      library_count=sum(1 for c in coll.parts if c.source == "library"),
                                      collection=coll)
        await self.seerr.enrich([c for c, _ in picks])
        return Recommendation(intro=sel.intro, picks=picks,
                              library_count=sum(1 for c, _ in picks if c.source == "library"))

    async def _collection(self, picks: list[Candidate]) -> FilmCollection | None:
        """The film series the best matching movie belongs to (its details name the collection), or None –
        then the single-title card is shown instead."""
        movie = next((c for c in picks if c.media_type == "movie"), None)
        if movie is None:
            return None
        try:
            ref = (await self.seerr.details("movie", movie.tmdb_id)).get("collection") or {}
            coll = await self.seerr.collection(int(ref["id"])) if ref.get("id") else None
        except httpx.HTTPError as e:
            log.warning(t("log.details_failed", title=movie.label, error=e))
            return None
        if coll is None or not coll.parts:
            log.info(t("log.no_collection", title=movie.label))
            return None
        if not coll.poster_url:  # no poster of its own: the one from the details, else the movie's
            coll.poster_url = f"{POSTER_BASE}{ref['posterPath']}" if ref.get("posterPath") else movie.poster_url
        log.info(t("log.collection", name=coll.name, n=len(coll.parts),
                   missing=sum(1 for c in coll.parts if c.source == "new")))
        return coll

    async def _collection_intro(self, request: str, coll: FilmCollection, speaker: str) -> str:
        def labels(source: str) -> list[str]:
            return [c.label for c in coll.parts if c.source == source]
        return await self.llm.say(t("sit.collection"), speaker, {
            "request": request, "series": coll.name, "in_library": labels("library"),
            "requested": labels("pending"), "missing": labels("new")})

    async def _new_titles(self, und: Understanding, media_types: list[str], exclude: set[str],
                          recent: set[str]) -> list[Candidate]:
        profile = await self.profiles.get()
        result: list[Candidate] = []
        for mt in media_types:
            ids = und.tmdb_movie_genre_ids if mt == "movie" else und.tmdb_tv_genre_ids
            try:
                found = await self.seerr.discover(mt, ids, exclude, want=12)
            except Exception as e:
                log.warning(t("log.discover_failed", type=mt, error=e))
                continue
            await self.seerr.enrich(found)
            for c in found:
                c.score = profile.score(c) - (1.0 if c.key in recent else 0.0)
            result += found
        return result
