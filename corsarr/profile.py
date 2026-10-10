"""Shared taste profile from Jellyfin history, favourites and stored feedback."""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .db import DB
from .i18n import t
from .jellyfin import Jellyfin, item_label
from .models import Candidate

log = logging.getLogger(__name__)


@dataclass
class Taste:
    """Signals from ratings and traits: shared ones, or those of one person."""
    genres: dict[str, float] = field(default_factory=dict)
    keywords: dict[str, float] = field(default_factory=dict)
    traits: list[dict] = field(default_factory=list)
    liked: list[str] = field(default_factory=list)
    disliked: list[str] = field(default_factory=list)

    def score(self, c: Candidate) -> float:
        genres = [g.lower() for g in c.genres]
        s = sum(self.genres.get(g, 0.0) for g in genres) / len(genres) if genres else 0.0
        kws = set(c.keywords)
        s += sum(self.keywords.get(k, 0.0) for k in kws)
        for tr in self.traits:
            hit = any(tk == k or (len(tk) > 3 and tk in k) for tk in tr["keywords"] for k in kws) \
                or tr["trait"].lower() in genres
            if hit:
                s += (2.0 if tr["direction"] == "more" else -2.0) * tr["weight"]
        return s


@dataclass
class Profile:
    genres: dict[str, float] = field(default_factory=dict)
    keywords: dict[str, float] = field(default_factory=dict)
    traits: list[dict] = field(default_factory=list)
    liked: list[str] = field(default_factory=list)      # favourites and 👍 ratings
    disliked: list[str] = field(default_factory=list)   # 👎 ratings
    recent_movies: list[str] = field(default_factory=list)  # last watched, newest first
    recent_series: list[str] = field(default_factory=list)
    people: dict[str, Taste] = field(default_factory=dict)  # first name -> their own ratings and traits

    def score(self, c: Candidate) -> float:
        """Shared taste (watch history, older ratings without a name) plus a fair compromise of the people:
        the average of their scores, and what one of them clearly dislikes pulls the title down."""
        s = Taste(self.genres, self.keywords, self.traits).score(c)
        if self.people:
            scores = [p.score(c) for p in self.people.values()]
            s += sum(scores) / len(scores) + 0.5 * min(0.0, min(scores))
        if c.rating:
            s += (float(c.rating) - 6.5) / 10
        return s

    def taste_for_prompt(self) -> dict:
        """What the model sees about their taste; per person only when there are at least two."""
        taste = {"liked": self.liked, "disliked": self.disliked,
                 "recent_movies": self.recent_movies, "recent_series": self.recent_series}
        if len(self.people) >= 2:
            taste["per_person"] = {
                name: {"liked": p.liked, "disliked": p.disliked,
                       "more_of": [tr["trait"] for tr in p.traits if tr["direction"] == "more"],
                       "less_of": [tr["trait"] for tr in p.traits if tr["direction"] == "less"]}
                for name, p in self.people.items()}
        return taste


class ProfileBuilder:
    TTL = 1800

    def __init__(self, db: DB, jellyfin: Jellyfin):
        self.db = db
        self.jellyfin = jellyfin
        self._cache: tuple[float, Profile | None] = (0.0, None)

    def invalidate(self) -> None:
        self._cache = (0.0, None)

    async def get(self) -> Profile:
        ts, prof = self._cache
        if prof and time.monotonic() - ts < self.TTL:
            return prof
        prof = build_profile(await self._history(), self.db.all_feedback(), self.db.traits())
        self._cache = (time.monotonic(), prof)
        return prof

    async def _history(self) -> list[dict]:
        try:
            return await self.jellyfin.taste_items()
        except Exception as e:  # profile still works from feedback alone
            log.warning(t("log.history_failed", error=e))
            return []


def build_profile(history: list[dict], feedback: list[dict], traits: list[dict]) -> Profile:
    counts: Counter[str] = Counter()
    for it in history:
        for g in it.get("Genres") or []:
            counts[g.lower()] += 2 if it.get("_favorite") else 1
    top = max(counts.values(), default=0)
    genres: dict[str, float] = defaultdict(float)
    for g, n in counts.items():
        genres[g] = n / top  # 0..1, history only pulls towards what they already watch

    # Ratings and traits without a name (from before per-person taste) count for everyone.
    shared = _signals([fb for fb in feedback if not fb.get("rater_name")],
                      [tr for tr in traits if not tr.get("rater_name")])
    for g, v in shared.genres.items():
        genres[g] += v
    names = list(dict.fromkeys(r["rater_name"] for r in feedback + traits if r.get("rater_name")))
    people = {name: _signals([fb for fb in feedback if fb.get("rater_name") == name],
                             [tr for tr in traits if tr.get("rater_name") == name]) for name in names}

    liked = [item_label(it) for it in history if it.get("_favorite")]
    liked += [fb["title"] for fb in feedback if fb.get("title") and fb["rating"] > 0 and fb["weight"] >= 1]
    disliked = [fb["title"] for fb in feedback if fb.get("title") and fb["rating"] < 0]
    # Separate lists: otherwise many watched movies push every series out of the limit.
    watched = [it for it in history if not it.get("_favorite")]
    recent_movies = [item_label(it) for it in watched if it.get("Type") != "Series"]
    recent_series = [item_label(it) for it in watched if it.get("Type") == "Series"]
    return Profile(genres=dict(genres), keywords=shared.keywords, traits=shared.traits,
                   liked=_unique(liked)[:HISTORY_TITLES], disliked=_unique(disliked)[:HISTORY_TITLES],
                   recent_movies=_unique(recent_movies)[:HISTORY_TITLES],
                   recent_series=_unique(recent_series)[:HISTORY_TITLES], people=people)


def _signals(feedback: list[dict], traits: list[dict]) -> Taste:
    genres: dict[str, float] = defaultdict(float)
    keywords: dict[str, float] = defaultdict(float)
    for fb in feedback:
        signal = fb["rating"] * fb["weight"]
        for g in fb["genres"]:
            genres[g.lower()] += 0.5 * signal
        for k in fb["keywords"]:
            keywords[k.lower()] += 0.3 * signal
    return Taste(genres=dict(genres), keywords=dict(keywords), traits=traits,
                 liked=_unique([fb["title"] for fb in feedback if fb.get("title") and fb["rating"] > 0])[:HISTORY_TITLES],
                 disliked=_unique([fb["title"] for fb in feedback if fb.get("title") and fb["rating"] < 0])[:HISTORY_TITLES])


HISTORY_TITLES = 20  # per list – enough for "similar to …", small enough for the prompt


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))
