from __future__ import annotations

from dataclasses import dataclass, field


def title_key(media_type: str, tmdb_id: int | str | None, jellyfin_id: str | None = None) -> str:
    """Stable identity across Jellyfin and TMDB: prefer the TMDB id."""
    if tmdb_id:
        return f"{media_type}:{int(tmdb_id)}"
    return f"jf:{jellyfin_id}"


@dataclass
class Candidate:
    media_type: str  # "movie" | "tv"
    source: str  # "library" | "new" | "pending" (already requested, downloading)
    title: str
    year: int | None = None
    jellyfin_id: str | None = None
    tmdb_id: int | None = None
    overview: str = ""
    runtime_min: int | None = None
    rating: float | None = None
    genres: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    poster_url: str | None = None  # public URL (TMDB) – Telegram fetches it itself
    seasons: int | None = None
    trailer_url: str | None = None  # YouTube link from Jellyfin or TMDB
    score: float = 0.0
    votes: int | None = None  # TMDB vote count – how well known a title is (helps tell search hits apart)

    @property
    def key(self) -> str:
        return title_key(self.media_type, self.tmdb_id, self.jellyfin_id)

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title
