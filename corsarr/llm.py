"""Language model: understanding requests, picking titles, feedback traits, persona texts.

Claude (Haiku 5.5) is the tested and recommended provider. OpenAI, Gemini, Ollama and LM Studio are
offered as well but untested.
"""
from __future__ import annotations

import collections
import json
import logging
import re
from typing import Literal, Optional

import anthropic
import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from . import persona
from .i18n import language, t
from .config import DEFAULT_URLS, LOCAL_PROVIDERS, PROVIDER_NAMES
from .monitor import describe_error, health
from .models import Candidate

log = logging.getLogger(__name__)


RECOMMENDED_MODEL = "claude-haiku-5-5"
# $ per million input / output tokens (Anthropic list prices, October 2026). Only these models are offered
# in the picker: each supports what the bot sends (effort, structured outputs). Older ones (e.g. Haiku 4.5)
# reject the effort setting.
MODEL_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
}
SUGGESTION_COST_HAIKU = 0.002  # $ per suggestion request, measured on real use with Claude Haiku 5.5
CACHE_READ_FACTOR, CACHE_WRITE_FACTOR = 0.1, 1.25  # prompt caching: share of the input price


def usage_cost(provider: str, model: str, usage: dict) -> float | None:
    """Estimated $ of one call from its token counts; 0 for local models, None when the price is unknown."""
    if provider in LOCAL_PROVIDERS:
        return 0.0
    if provider != "claude" or model not in MODEL_PRICES:
        return None
    inp, out = MODEL_PRICES[model]
    tokens_in = (usage.get("input", 0) + CACHE_READ_FACTOR * usage.get("cache_read", 0)
                 + CACHE_WRITE_FACTOR * usage.get("cache_write", 0))
    return (tokens_in * inp + usage.get("output", 0) * out) / 1_000_000


def model_cost(model_id: str) -> dict | None:
    """Price and how many times more expensive than the recommended model."""
    if model_id not in MODEL_PRICES:
        return None
    inp, out = MODEL_PRICES[model_id]
    factor = round(out / MODEL_PRICES[RECOMMENDED_MODEL][1])
    return {"input": inp, "output": out, "factor": factor,
            "per_suggestion": round(SUGGESTION_COST_HAIKU * factor, 4)}


async def available_claude_models(api_key: str) -> list[dict]:
    """Supported Claude models this API key may use, recommended first, then cheapest first."""
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=1, timeout=15)
    models = []
    try:
        async for m in client.models.list():
            if m.id in MODEL_PRICES:
                models.append({"id": m.id, "name": m.display_name, "cost": model_cost(m.id),
                               "recommended": m.id == RECOMMENDED_MODEL})
    except anthropic.APIError as e:
        raise LLMUnavailable(_claude_error(e)) from e
    finally:
        await client.close()
    models.sort(key=lambda m: (not m["recommended"], m["cost"]["factor"]))
    return models


class LLMUnavailable(Exception):
    """API unreachable or spend limit reached – the bot goes into outage mode."""


class BudgetReached(LLMUnavailable):
    """The monthly budget set in the web interface is used up – handled like an outage until it is raised
    or the month is over (the outage probe keeps failing without spending anything)."""


class LLMFailed(Exception):
    """A single call produced no usable answer (refusal, truncated output)."""


# --- structured outputs --------------------------------------------------

class SettingsChange(BaseModel):
    series_pause_days: Optional[int] = Field(None, description="days until a series counts as paused")
    abort_days: Optional[int] = Field(None, description="days until the abort follow-up")
    characters_on: list[str] = Field(default_factory=list, description="ids of characters to switch on")
    characters_off: list[str] = Field(default_factory=list, description="ids of characters to switch off")
    streaming_set: list[str] = Field(default_factory=list, description=(
        "streaming services they say they have (replaces the list), names as written"))
    streaming_add: list[str] = Field(default_factory=list, description="streaming services they got")
    streaming_remove: list[str] = Field(default_factory=list, description="streaming services they no longer have")


class FeedbackIntent(BaseModel):
    title_key: Optional[str] = Field(None, description="key from the list of recent titles, if it can be matched")
    rating: Literal["up", "meh", "down", "none"]
    text: str = Field(description="substance of the free-text feedback, empty if none")


class Understanding(BaseModel):
    language: str = Field("en", description="ISO 639-1 code of the language the message is written in, e.g. de, en")
    intent: Literal["recommend", "new_only", "lookup", "settings", "feedback", "chat"]
    max_runtime_min: Optional[int] = Field(None, description=(
        "only when they state a time limit: longest runtime in minutes ('max 2 hours' -> 120; series: per episode)"))
    search_queries: list[str] = Field(default_factory=list, description=(
        "lookup only: 1–4 TMDB search terms – your best guess of the exact title first, then short keyword "
        "combinations from the message"))
    whole_collection: bool = Field(False, description=(
        "lookup only: they want all parts of a film series (e.g. all Mission: Impossible films)"))
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


class TranslatedText(BaseModel):
    key: str
    text: str


class Translated(BaseModel):
    items: list[TranslatedText]

    @model_validator(mode="before")
    @classmethod
    def _plain_mapping(cls, data):
        # Smaller models often answer {"bot.x": "…"} instead of the requested list – accept that too.
        if isinstance(data, dict) and "items" not in data and all(isinstance(v, str) for v in data.values()):
            return {"items": [{"key": k, "text": v} for k, v in data.items()]}
        return data


class TraitResult(BaseModel):
    traits: list[Trait]
    reply: str = Field(description="short confirmation in the speaker's style")


class _Claude:
    """Anthropic's API through the official SDK – the tested and recommended provider."""
    provider = "claude"
    last_usage: dict | None = None

    def __init__(self, api_key: str, model: str):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2, timeout=45)
        self.model = model

    async def generate(self, system: str, user: str, output_format: type[BaseModel] | None, max_tokens: int):
        try:
            kwargs = dict(model=self.model, max_tokens=max_tokens,
                          # Stable prefix (instructions, characters, genre lists) – cached across calls.
                          system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                          output_config={"effort": "low"},
                          messages=[{"role": "user", "content": user}])
            if output_format is None:
                resp = await self.client.messages.create(**kwargs)
            else:
                resp = await self.client.messages.parse(output_format=output_format, **kwargs)
        except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise LLMUnavailable(_claude_error(e)) from e
        except anthropic.NotFoundError as e:  # an unknown model id – every call would fail the same way
            raise LLMUnavailable(f"model {self.model} not found – check the model name ({_claude_error(e)})") from e
        except anthropic.BadRequestError as e:
            msg = str(e).lower()
            if "usage limit" in msg or "credit balance" in msg or "billing" in msg:
                raise LLMUnavailable(_claude_error(e)) from e
            raise LLMFailed(_claude_error(e)) from e  # this request was refused (e.g. too long) – not an outage
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise LLMUnavailable(_claude_error(e)) from e
            raise LLMFailed(_claude_error(e)) from e
        except anthropic.APIError as e:  # anything else the SDK raises (e.g. an unexpected response)
            raise LLMFailed(_claude_error(e)) from e
        health.ok("llm")

        u = resp.usage
        self.last_usage = {"input": u.input_tokens or 0, "cache_read": u.cache_read_input_tokens or 0,
                           "cache_write": u.cache_creation_input_tokens or 0, "output": u.output_tokens or 0}
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

    async def check_model(self) -> None:
        try:
            await self.client.models.retrieve(self.model)
        except anthropic.APIError as e:
            raise LLMUnavailable(_claude_error(e)) from e

    async def close(self) -> None:
        await self.client.close()


def _claude_error(e: anthropic.APIError) -> str:
    """'HTTP 401: invalid x-api-key' instead of the SDK's full error dict."""
    if isinstance(e, anthropic.APIStatusError):
        body = e.body if isinstance(e.body, dict) else {}
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        return f"HTTP {e.status_code}: {err.get('message') or e.message}"
    return describe_error(e)


# Substrings of model ids that are not chat models (embeddings, speech, images, …).
NOT_CHAT = ("embed", "tts", "whisper", "dall-e", "audio", "realtime", "transcribe", "moderation", "image",
            "imagen", "veo", "aqa", "davinci", "babbage", "search")


class _OpenAICompatible:
    """OpenAI, Gemini, Ollama and LM Studio through the OpenAI-compatible chat completions API.

    UNTESTED: developed against the documented API only. Structured answers are requested as a JSON
    schema and, since not every server honours that, the schema is also spelled out in the prompt.
    """

    last_usage: dict | None = None

    def __init__(self, provider: str, model: str, api_key: str = "", url: str = ""):
        self.provider, self.model = provider, model
        base = (url or DEFAULT_URLS[provider]).rstrip("/")
        if provider in LOCAL_PROVIDERS and not base.endswith("/v1"):
            base += "/v1"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # Local models on modest hardware can take minutes for a long answer.
        self.client = httpx.AsyncClient(base_url=base, headers=headers,
                                        timeout=300 if provider in LOCAL_PROVIDERS else 90)

    async def _post(self, body: dict) -> dict:
        try:
            r = await self.client.post("/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMUnavailable(describe_error(e)) from e
        if r.status_code == 400:
            raise _BadRequest(_error_text(r))
        if r.status_code == 404:  # wrong URL, or a model the server doesn't have
            raise LLMUnavailable(f"HTTP 404: {_error_text(r)} – check the URL and the model name {self.model}")
        if r.status_code >= 400:  # key, quota, server
            raise LLMUnavailable(f"HTTP {r.status_code}: {_error_text(r)}")
        try:
            return r.json()
        except ValueError as e:
            raise LLMFailed("response is not JSON") from e

    async def generate(self, system: str, user: str, output_format: type[BaseModel] | None, max_tokens: int):
        if output_format is not None:
            schema = _inline_schema(output_format.model_json_schema())
            user += "\n\n" + JSON_INSTRUCTION + json.dumps(schema, ensure_ascii=False)
        body = {"model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                # Reasoning models count their thinking against this limit, so leave plenty of room.
                ("max_completion_tokens" if self.provider == "openai" else "max_tokens"): max_tokens * 4}
        if output_format is not None:
            body["response_format"] = {"type": "json_schema", "json_schema": {
                "name": output_format.__name__, "schema": schema, "strict": False}}
        try:
            data = await self._post(body)
        except _BadRequest as e:
            if "response_format" not in body:
                raise LLMFailed(str(e)) from e
            # Some servers or models don't support JSON schemas – the prompt still describes the format.
            log.info("LLM %s: retrying without response_format (%s)", self.provider, e)
            body.pop("response_format")
            try:
                data = await self._post(body)
            except _BadRequest as e2:
                raise LLMFailed(str(e2)) from e2
        health.ok("llm")

        try:
            choice = data["choices"][0]
            message = choice["message"]
            finish, refusal, content = choice.get("finish_reason"), message.get("refusal"), message.get("content")
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMFailed("unexpected response") from e
        usage = data.get("usage") or {}
        self.last_usage = {"input": usage.get("prompt_tokens") or 0, "cache_read": 0, "cache_write": 0,
                           "output": usage.get("completion_tokens") or 0}
        log.info("LLM %s %s: in=%s out=%s stop=%s", self.provider,
                 output_format.__name__ if output_format else "text", usage.get("prompt_tokens"),
                 usage.get("completion_tokens"), finish)
        if finish == "length":
            raise LLMFailed("max_tokens")
        if refusal:
            raise LLMFailed("refusal")
        text = THINK.sub("", content or "").strip()
        if output_format is None:
            return text
        return _parse_json(text, output_format)

    async def list_models(self) -> list[str]:
        try:
            r = await self.client.get("/models")
            r.raise_for_status()
            ids = [str(m["id"]).removeprefix("models/") for m in r.json().get("data", [])]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as e:
            raise LLMUnavailable(describe_error(e)) from e
        ids = [i for i in ids if not any(s in i.lower() for s in NOT_CHAT)]
        if self.provider == "gemini":
            ids = [i for i in ids if "gemini" in i]
        return sorted(set(ids))

    async def check_model(self) -> None:
        ids = await self.list_models()
        if self.model not in ids and f"{self.model}:latest" not in ids:  # Ollama tags
            raise LLMUnavailable(f"model {self.model} not found (available: {', '.join(ids[:10]) or '–'})")

    async def close(self) -> None:
        await self.client.aclose()


class _BadRequest(Exception):
    """HTTP 400 – the request itself was refused (unsupported parameter, malformed schema …)."""


JSON_INSTRUCTION = "Answer with a single JSON object only, no other text, matching this JSON schema: "
THINK = re.compile(r"<think>.*?</think>", re.S)  # reasoning some local models put into the answer


def _error_text(r: httpx.Response) -> str:
    try:
        err = r.json().get("error")
    except (ValueError, AttributeError):
        return r.text[:200]
    if isinstance(err, list) and err:
        err = err[0].get("error", err[0]) if isinstance(err[0], dict) else err[0]
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err or r.text)[:300]


def _inline_schema(schema: dict) -> dict:
    """Resolve $ref/$defs and drop titles: small servers and Gemini handle flat schemas better."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return walk(schema)


def _parse_json(text: str, output_format: type[BaseModel]) -> BaseModel:
    """The JSON object in a reply, also when the model wrapped it in a code fence or added a sentence."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMFailed("no JSON in the answer")
    try:
        return output_format.model_validate_json(text[start:end + 1])
    except ValidationError as e:
        raise LLMFailed(f"answer does not match the format: {e.error_count()} errors") from e


def create(cfg) -> "LLM":
    """The language model the configuration selects."""
    if cfg.llm_provider == "claude":
        return LLM(_Claude(cfg.llm_api_key, cfg.model))
    return LLM(_OpenAICompatible(cfg.llm_provider, cfg.model, cfg.llm_api_key, cfg.llm_url))


async def available_models(provider: str, api_key: str = "", url: str = "") -> list[dict]:
    """Models for the picker: Claude with prices (recommended first), the others as plain ids."""
    if provider == "claude":
        return await available_claude_models(api_key)
    backend = _OpenAICompatible(provider, "", api_key, url)
    try:
        ids = await backend.list_models()
    finally:
        await backend.close()
    return [{"id": i, "name": i, "cost": None, "recommended": False} for i in ids]


class LLM:
    """What the bot asks the language model; the provider behind it is interchangeable."""

    def __init__(self, backend):
        self.backend = backend
        self.on_usage = None         # callback(kind, token counts) after every call – usage tracking
        self.budget_reached = None   # callback() -> reason text when the monthly budget is used up
        self.genre_context = ""
        self.tmdb_genre_names: dict[int, str] = {}
        # Last lines the characters said; fed back so they don't repeat themselves.
        self.recent_lines: collections.deque[str] = collections.deque(maxlen=15)

    provider = property(lambda self: self.backend.provider)
    model = property(lambda self: self.backend.model)
    label = property(lambda self: PROVIDER_NAMES[self.backend.provider])

    async def close(self) -> None:
        await self.backend.close()

    def _remember(self, *texts: str) -> None:
        for text in texts:
            for line in text.splitlines():
                line = persona.strip_emoji(line)[1]
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

    def _system(self) -> str:
        # The language line is last: with prompts in English it is what makes the model answer in French etc.
        return (f"{t('prompt.base', characters=persona.characters())}\n\n{self.genre_context}\n\n"
                f"{t('prompt.output_language', lang=language())}")

    async def _call(self, user: str, output_format: type[BaseModel] | None, max_tokens: int = 2000):
        if self.budget_reached and (reason := self.budget_reached()):
            health.error("llm", reason)
            raise BudgetReached(reason)
        self.backend.last_usage = None
        try:
            return await self.backend.generate(self._system(), user, output_format, max_tokens)
        except LLMUnavailable as e:
            health.error("llm", str(e))
            raise
        finally:  # also when the answer was unusable: the tokens were spent anyway
            if self.backend.last_usage and self.on_usage:
                self.on_usage(output_format.__name__ if output_format else "text", self.backend.last_usage)

    async def ping(self) -> None:
        """A real (tiny) generation – also detects a reached spending limit."""
        try:
            await self._call(t("prompt.ping"), None, max_tokens=50)
        except LLMFailed:
            pass  # it answered, just not usefully (e.g. a reasoning model ran out of tokens) – reachable

    async def check_model(self) -> None:
        """Free check for the periodic status: key valid and model available, no tokens used."""
        try:
            await self.backend.check_model()
        except LLMUnavailable as e:
            health.error("llm", str(e))
            raise

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
                **({"streaming_on": c.streaming} if c.streaming else {}),
            }
            for i, c in enumerate(cands)
        ]
        genres_note = t("prompt.genres_wanted", genres=", ".join(genres)) if genres else ""
        taste = {k: v for k, v in (taste or {}).items() if v}
        taste_note = t("prompt.taste", taste=json.dumps(taste, ensure_ascii=False)) if taste else ""
        if taste.get("per_person"):
            taste_note += t("prompt.taste_people")
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

    async def identify(self, request: str, cands: list[Candidate], speaker: str) -> Selection:
        """Which search hits are the title they asked about (usually one, at most three)."""
        status = {"library": t("prompt.source_library"), "pending": t("prompt.source_pending"),
                  "new": t("prompt.source_new")}
        listing = [{"id": i, "title": c.label, "type": c.media_type, "status": status[c.source],
                    "votes": c.votes, "overview": c.overview[:300]} for i, c in enumerate(cands)]
        prompt = t("prompt.lookup", speaker=persona.speaker_instruction(speaker), request=request,
                   candidates=json.dumps(listing, ensure_ascii=False))
        sel: Selection = await self._call(self._avoid() + prompt, Selection, max_tokens=1500)
        sel.intro = persona.enforce(sel.intro, speaker)
        for p in sel.picks:
            p.reason = persona.enforce(p.reason.splitlines()[0] if p.reason else "", speaker)
        self._remember(sel.intro, *(p.reason for p in sel.picks))
        return sel

    async def translate(self, lang: str, texts: dict[str, str]) -> dict[str, str]:
        """Fixed Telegram texts from English into `lang` (see translate.py)."""
        prompt = t("prompt.translate", lang=lang, texts=json.dumps(texts, ensure_ascii=False))
        res: Translated = await self._call(prompt, Translated, max_tokens=8000)
        return {item.key: item.text for item in res.items}

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
