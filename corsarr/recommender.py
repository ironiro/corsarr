"""5–6 suggestions per request: at most 2 from the library (shown first), the rest new via Jellyseerr."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .db import DB
from .i18n import t
from .jellyfin import Jellyfin
from .jellyseerr import Jellyseerr
from .llm import LLM, Understanding
from .models import Candidate
from .profile import ProfileBuilder

log = logging.getLogger(__name__)

PICKS_MIN, PICKS_MAX = 5, 6
MAX_LIBRARY_PICKS = 2  # library titles come first, but only a taste of them – the rest is new
LIBRARY_POOL = 8       # candidates the model sees per source
NEW_POOL = 20

def genre_match(c: Candidate, wanted: set[str]) -> int:
    """How many of the requested genres the title carries (names compared case-insensitively)."""
    return sum(1 for g in c.genres if g.lower() in wanted)


@dataclass
class Recommendation:
    intro: str
    picks: list[tuple[Candidate, str]]
    library_count: int


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
