"""Claude Haiku 5.5: understanding requests, picking titles, feedback traits, persona texts."""
from __future__ import annotations

import collections
import json
import logging
from typing import Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from . import persona
from .i18n import t
from .monitor import health
from .models import Candidate

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """API unreachable or spend limit reached – the bot goes into outage mode."""


class LLMFailed(Exception):
    """A single call produced no usable answer (refusal, truncated output)."""


# --- structured outputs --------------------------------------------------

class SettingsChange(BaseModel):
    series_pause_days: Optional[int] = Field(None, description="days until a series counts as paused")
    abort_days: Optional[int] = Field(None, description="days until the abort follow-up")
    pirate_enabled: Optional[bool] = None
    genz_enabled: Optional[bool] = None


class FeedbackIntent(BaseModel):
    title_key: Optional[str] = Field(None, description="key from the list of recent titles, if it can be matched")
    rating: Literal["up", "meh", "down", "none"]
    text: str = Field(description="substance of the free-text feedback, empty if none")


class Understanding(BaseModel):
    language: str = Field("en", description="ISO 639-1 code of the language the message is written in, e.g. de, en")
    intent: Literal["recommend", "new_only", "settings", "feedback", "chat"]
    media_types: list[Literal["movie", "tv"]] = Field(description="empty = movies and series")
    jellyfin_genres: list[str] = Field(description="only names from the Jellyfin genre list")
    tmdb_movie_genre_ids: list[int]
    tmdb_tv_genre_ids: list[int]
    settings: SettingsChange
    feedback: FeedbackIntent


class Pick(BaseModel):
    id: int
    reason: str = Field(description="one line 'fits because …' in the speaker's style")


class Selection(BaseModel):
    intro: str = Field(description="intro in the speaker's style, 1–3 lines")
    picks: list[Pick]


class Trait(BaseModel):
    direction: Literal["more", "less"]
    trait: str = Field(description="short trait in the chat language, e.g. 'gore'")
    tmdb_keywords: list[str] = Field(description="matching English TMDB keywords, lowercase")


class TraitResult(BaseModel):
    traits: list[Trait]
    reply: str = Field(description="short confirmation in the speaker's style")


class LLM:
    def __init__(self, api_key: str, model: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=45)
        self.model = model
        self.genre_context = ""
        self.tmdb_genre_names: dict[int, str] = {}
        # Last lines the characters said; fed back so they don't repeat themselves.
        self.recent_lines: collections.deque[str] = collections.deque(maxlen=15)

    def _remember(self, *texts: str) -> None:
        for text in texts:
            for line in text.splitlines():
                line = line.removeprefix(persona.PIRATE).removeprefix(persona.GENZ).strip()
                if line:
                    self.recent_lines.append(line[:120])

    def _avoid(self) -> str:
        if not self.recent_lines:
            return ""
        return t("prompt.avoid", phrases=json.dumps(list(self.recent_lines), ensure_ascii=False))

    def requested_genres(self, und: "Understanding") -> list[str]:
        """Names of all genres the request asked for (Jellyfin names plus TMDB ids resolved)."""
        names = list(und.jellyfin_genres)
        names += [self.tmdb_genre_names[i] for i in und.tmdb_movie_genre_ids + und.tmdb_tv_genre_ids
                  if i in self.tmdb_genre_names]
        return list(dict.fromkeys(names))

    def set_genres(self, jellyfin: list[str], tmdb_movie: list[dict], tmdb_tv: list[dict]) -> None:
        self.tmdb_genre_names = {g["id"]: g["name"] for g in tmdb_movie + tmdb_tv}
        self.genre_context = (
            f"{t('prompt.genres_jellyfin')}: {json.dumps(jellyfin, ensure_ascii=False)}\n"
            f"{t('prompt.genres_tmdb_movie')}: "
            f"{json.dumps({g['id']: g['name'] for g in tmdb_movie}, ensure_ascii=False)}\n"
            f"{t('prompt.genres_tmdb_tv')}: "
            f"{json.dumps({g['id']: g['name'] for g in tmdb_tv}, ensure_ascii=False)}"
        )

    def _system(self) -> list[dict]:
        # Stable prefix (instructions, characters, genre lists) – cached across calls.
        base = t("prompt.base", characters=persona.characters())
        return [{"type": "text", "text": f"{base}\n\n{self.genre_context}",
                 "cache_control": {"type": "ephemeral"}}]

    async def _call(self, user: str, output_format: type[BaseModel] | None, max_tokens: int = 2000):
        try:
            return await self._call_api(user, output_format, max_tokens)
        except LLMUnavailable as e:
            health.error("claude", str(e))
            raise

    async def _call_api(self, user: str, output_format: type[BaseModel] | None, max_tokens: int):
        try:
            kwargs = dict(model=self.model, max_tokens=max_tokens, system=self._system(),
                          output_config={"effort": "low"},
                          messages=[{"role": "user", "content": user}])
            if output_format is None:
                resp = await self.client.messages.create(**kwargs)
            else:
                resp = await self.client.messages.parse(output_format=output_format, **kwargs)
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LLMUnavailable(str(e)) from e
        except anthropic.BadRequestError as e:
            msg = str(e).lower()
            if "usage limit" in msg or "credit balance" in msg or "billing" in msg:
                raise LLMUnavailable(str(e)) from e
            raise
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise LLMUnavailable(str(e)) from e
            raise
        health.ok("claude")

        u = resp.usage
        log.info("LLM %s: in=%s cache_read=%s cache_write=%s out=%s stop=%s",
                 output_format.__name__ if output_format else "text", u.input_tokens,
                 u.cache_read_input_tokens, u.cache_creation_input_tokens, u.output_tokens,
                 resp.stop_reason)
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise LLMFailed(resp.stop_reason)
        if output_format is not None:
            if resp.parsed_output is None:
                raise LLMFailed("no parsed output")
            return resp.parsed_output
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    async def ping(self) -> None:
        """A real (tiny) generation – also detects a reached spending limit."""
        await self._call(t("prompt.ping"), None, max_tokens=50)

    async def check_model(self) -> None:
        """Free check for the periodic status: key valid and model available, no tokens used."""
        try:
            await self.client.models.retrieve(self.model)
        except anthropic.APIError as e:
            raise LLMUnavailable(str(e)) from e

    async def understand(self, text: str, recent_titles: list[dict]) -> Understanding:
        recent = json.dumps([{"key": r["title_key"], "title": r["title"]} for r in recent_titles],
                            ensure_ascii=False)
        prompt = (f"{t('prompt.understand')}\n\n{t('prompt.recent_titles')}: {recent}\n\n"
                  f"{t('prompt.message')}: {text}")
        return await self._call(prompt, Understanding, max_tokens=1500)

    async def select(self, request: str, cands: list[Candidate], speaker: str, n_min: int, n_max: int,
                     notes: str, genres: list[str] | None = None, taste: dict | None = None) -> Selection:
        listing = [
            {
                "id": i, "title": c.label, "type": c.media_type,
                "source": t("prompt.source_library") if c.source == "library" else t("prompt.source_new"),
                "genres": c.genres[:5], "keywords": c.keywords[:8],
                "overview": c.overview[:300], "rating": c.rating, "profile_score": round(c.score, 2),
            }
            for i, c in enumerate(cands)
        ]
        genres_note = t("prompt.genres_wanted", genres=", ".join(genres)) if genres else ""
        taste = {k: v for k, v in (taste or {}).items() if v}
        taste_note = t("prompt.taste", taste=json.dumps(taste, ensure_ascii=False)) if taste else ""
        prompt = t("prompt.select", n_min=n_min, n_max=n_max, notes=notes,
                   speaker=persona.speaker_instruction(speaker), request=request,
                   genres_note=genres_note, taste_note=taste_note,
                   candidates=json.dumps(listing, ensure_ascii=False))
        sel: Selection = await self._call(self._avoid() + prompt, Selection, max_tokens=3000)
        sel.intro = persona.enforce(sel.intro, speaker)
        for p in sel.picks:
            p.reason = persona.enforce(p.reason.splitlines()[0] if p.reason else "", speaker)
        self._remember(sel.intro, *(p.reason for p in sel.picks))
        return sel

    async def feedback_traits(self, title: str, rating: str, text: str, genres: list[str],
                              keywords: list[str], speaker: str) -> TraitResult:
        prompt = t("prompt.traits", speaker=persona.speaker_instruction(speaker), title=title,
                   rating=rating, genres=genres, keywords=keywords[:30], text=text)
        res: TraitResult = await self._call(self._avoid() + prompt, TraitResult, max_tokens=1500)
        res.reply = persona.enforce(res.reply, speaker)
        self._remember(res.reply)
        return res

    async def say(self, situation: str, speaker: str, facts: dict | None = None) -> str:
        prompt = t("prompt.say", speaker=persona.speaker_instruction(speaker), situation=situation)
        if facts:
            prompt += f"\n{t('prompt.facts')}: {json.dumps(facts, ensure_ascii=False, default=str)}"
        text = persona.enforce(await self._call(self._avoid() + prompt, None, max_tokens=800), speaker)
        self._remember(text)
        return text
