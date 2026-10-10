"""Telegram side: mentions/replies, suggestion cards, buttons, feedback questions, outage mode."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from datetime import timedelta

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from . import config, persona
from .cards import CardsMixin, log_request_failed
from .i18n import default_language, normalize, switch_language, t, use_language
from .jobs import JobsMixin, in_chat_language
from .monitor import health
from .config import Config
from .db import DEFAULT_SETTINGS, DB, iso, now
from .feedback import FeedbackService
from .jellyfin import Jellyfin
from .jellyseerr import Jellyseerr
from .llm import LLM, LLMFailed, LLMUnavailable, Understanding
from .translate import Translations
from .recommender import Recommender

log = logging.getLogger(__name__)

SETTING_LIMITS = {"series_pause_days": (1, 365), "abort_days": (1, 60)}
OUTAGE_REPLY_INTERVAL = 1800  # seconds between "not reachable" replies while the model is down
PRIVATE_CHATS_KEPT = 5        # people who wrote privately, offered as ADMIN_CHAT_ID in the web interface
INTENT_LOG_CHARS = 120        # how much of a message the intent log line shows
RATINGS_MARK = "🗳️"
RATING_EMOJI = {"up": "👍", "meh": "😐", "down": "👎"}


class CorsarrBot(CardsMixin, JobsMixin):
    def __init__(self, cfg: Config, db: DB, jellyfin: Jellyfin, seerr: Jellyseerr, llm: LLM,
                 recommender: Recommender, feedback: FeedbackService):
        self.cfg, self.db, self.jellyfin, self.seerr = cfg, db, jellyfin, seerr
        self.llm, self.recommender, self.feedback = llm, recommender, feedback
        self.translations = Translations(db, llm)  # fixed texts for languages without built-in ones
        self.on_config_change = lambda: None  # set by the runtime: reload settings changed in the chat
        self.app: Application | None = None
        self.bot_id: int | None = None
        self.username = ""
        self.down = False
        self._last_outage_reply = 0.0
        self._probing = False  # one outage probe at a time – else "back again" would be posted twice
        self.genres_loaded = False
        self._card_locks: dict[int, asyncio.Lock] = {}  # per carousel: one message edit at a time
        self._nav_wanted: dict[int, int] = {}           # per carousel: the page the newest tap asked for

    # --- helpers -------------------------------------------------------------
    @property
    def bot(self):
        return self.app.bot

    def speaker(self) -> str:
        return persona.choose_speaker(self.db.settings())

    async def post(self, text: str, **kwargs) -> Message:
        return await self.bot.send_message(self.cfg.chat_id, text, **kwargs)

    async def enter_outage(self, reason: str) -> None:
        log.warning(t("log.llm_down", provider=self.llm.label, reason=reason))
        if not self.down:
            self.down = True
            self._last_outage_reply = time.monotonic()
            await self.post(t("bot.outage"))

    async def load_genres(self) -> None:
        if self.genres_loaded:
            return
        try:
            jf = await self.jellyfin.genres()
            mv = await self.seerr.genres("movie")
            tv = await self.seerr.genres("tv")
        except Exception as e:
            log.warning(t("log.genres_failed", error=e))
            return
        self.llm.set_genres(jf, mv, tv)
        self.genres_loaded = True
        log.info(t("log.genres_loaded", jf=len(jf), mv=len(mv), tv=len(tv)))

    def _addressed(self, msg: Message) -> tuple[bool, str]:
        text = msg.text or ""
        reply = msg.reply_to_message
        replied = bool(reply and reply.from_user and reply.from_user.id == self.bot_id)
        mentioned = False
        for ent, value in msg.parse_entities([MessageEntity.MENTION, MessageEntity.TEXT_MENTION]).items():
            if (ent.type == MessageEntity.MENTION and value.lower() == f"@{self.username.lower()}") or \
                    (ent.user and ent.user.id == self.bot_id):
                mentioned = True
        clean = re.sub(rf"@{re.escape(self.username)}\b", "", text, flags=re.IGNORECASE).strip()
        return replied or mentioned, clean

    # --- incoming messages ---------------------------------------------------
    async def on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg or msg.chat_id != self.cfg.chat_id or not msg.text:
            return
        self._note_member(msg.from_user)
        addressed, text = self._addressed(msg)
        health.ok("telegram")
        if not addressed:
            return  # discarded before the model ever sees it
        # Start in the group's last language; switches once the message's own language is known.
        with use_language(self.chat_language()):
            await self._handle(msg, text, context)

    async def on_private(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Someone wrote to the bot privately: remember the chat so the admin can pick it in the web
        interface as ADMIN_CHAT_ID, and tell them how. Nothing else happens in private chats."""
        msg = update.effective_message
        user = update.effective_user
        if not msg or not user:
            return
        known = json.loads(self.db.get_state("private_chats") or "[]")
        known = [c for c in known if c["id"] != msg.chat_id][-(PRIVATE_CHATS_KEPT - 1):]
        known.append({"id": msg.chat_id, "name": user.full_name or user.username or str(user.id), "at": iso(now())})
        self.db.set_state("private_chats", json.dumps(known, ensure_ascii=False))
        log.info(t("log.private_chat", name=user.full_name, id=msg.chat_id))
        with use_language(user.language_code or self.chat_language()):
            await msg.reply_text(t("bot.private_hello", name=html.escape(user.first_name or ""), id=msg.chat_id),
                                 parse_mode=ParseMode.HTML)

    # --- the people in the group (for ratings per person) -------------------------
    def _note_member(self, user) -> None:
        """Remember who is in the group – everyone who writes to the bot or presses a button."""
        uid = getattr(user, "id", None)
        if uid is None or getattr(user, "is_bot", False):
            return
        members = json.loads(self.db.get_state("members") or "{}")
        name = getattr(user, "first_name", None) or getattr(user, "full_name", None) or str(uid)
        if members.get(str(uid), {}).get("name") != name:
            members[str(uid)] = {"name": name, "seen": iso(now())}
            self.db.set_state("members", json.dumps(members, ensure_ascii=False))

    def members(self) -> dict[str, str]:
        """id -> first name of the people known in the group."""
        return {k: v["name"] for k, v in json.loads(self.db.get_state("members") or "{}").items()}

    @staticmethod
    def _rater(user) -> tuple[int, str] | None:
        return (user.id, user.first_name or user.full_name or str(user.id)) if user else None

    def _all_rated(self, req: dict) -> bool:
        """A question stays open until everyone known in the group has rated (or it expires)."""
        return len(req["extra"].get("ratings") or {}) >= max(1, len(self.members()))

    def chat_language(self) -> str:
        """Language the group last wrote in – used for messages the bot sends on its own."""
        return self.db.get_state("chat_language") or default_language()

    async def _adopt_language(self, und: Understanding) -> None:
        """Answer in the language of the message – any language; fixed texts are translated once if needed."""
        lang = normalize(und.language)
        if not lang:
            return
        switch_language(lang)
        self.db.set_state("chat_language", lang)
        await self.translations.ensure(lang)

    async def _handle(self, msg: Message, text: str, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            if self.down and not await self._probe():
                if time.monotonic() - self._last_outage_reply > OUTAGE_REPLY_INTERVAL:
                    self._last_outage_reply = time.monotonic()
                    await msg.reply_text(t("bot.outage"))
                return
            await self.load_genres()
            await context.bot.send_chat_action(self.cfg.chat_id, ChatAction.TYPING)
            reply_to = msg.reply_to_message
            fb_req = self.db.request_by_message(reply_to.message_id) if reply_to else None
            if fb_req:
                await self._feedback_text(msg, fb_req, text)
                return
            und = await self.llm.understand(text or t("bot.hello"), self.db.recent_requests())
            await self._adopt_language(und)
            who = msg.from_user.first_name if msg.from_user else "?"
            short = text if len(text) <= INTENT_LOG_CHARS else text[:INTENT_LOG_CHARS - 1] + "…"
            log.info(t("log.intent", who=who, text=short, intent=und.intent, types=und.media_types or "*",
                       genres=self.llm.requested_genres(und) or "*"))
            if und.intent in ("recommend", "new_only"):
                await self._recommend(msg, text, und)
            elif und.intent == "lookup":
                await self._lookup(msg, text, und)
            elif und.intent == "settings":
                await self._settings(msg, und)
            elif und.intent == "feedback":
                await self._feedback_intent(msg, text, und)
            else:
                await self._chat(msg, text)
        except LLMUnavailable as e:
            await self.enter_outage(str(e))
        except LLMFailed as e:
            log.warning(t("log.llm_failed", provider=self.llm.label, error=e))
            await msg.reply_text(t("bot.failed"))
        except httpx.HTTPError as e:
            log.exception(t("log.backend_error"))
            await self._say_reply(msg, t("sit.backend_error"), {"error": type(e).__name__})
        except Exception:  # whatever else went wrong: they must not be left waiting for an answer
            log.exception(t("log.handle_failed"))
            try:
                await msg.reply_text(t("bot.failed"))
            except TelegramError as e:
                log.warning(t("log.not_editable", error=e))

    async def _say_reply(self, msg: Message, situation: str, facts: dict | None = None) -> None:
        try:
            await msg.reply_text(await self.llm.say(situation, self.speaker(), facts))
        except LLMUnavailable as e:
            await self.enter_outage(str(e))
        except LLMFailed:
            await msg.reply_text(t("bot.failed"))

    # --- recommendations -----------------------------------------------------
    async def _recommend(self, msg: Message, text: str, und: Understanding) -> None:
        speaker = self.speaker()
        rec = await self.recommender.recommend(text, und, speaker)
        if not rec.picks:
            log.info(t("log.nothing_found", request=text))
            await self._say_reply(msg, t("sit.nothing_found"), {"request": text})
            return
        await msg.reply_text(rec.intro)
        await self._send_carousel(rec.picks)
        library = sum(1 for c, _ in rec.picks if c.source == "library")
        log.info(t("log.suggested", n=len(rec.picks), library=library))

    async def _lookup(self, msg: Message, text: str, und: Understanding) -> None:
        rec = await self.recommender.lookup(text, und, self.speaker())
        if not rec.picks:
            await self._say_reply(msg, t("sit.lookup_none"), {"request": text, "searched": und.search_queries})
            return
        if rec.collection is not None:
            await self._send_collection(rec.collection, rec.intro)
            return
        await msg.reply_text(rec.intro)
        await self._send_carousel(rec.picks)

    # --- settings --------------------------------------------------------------
    async def _settings(self, msg: Message, und: Understanding) -> None:
        changes = {k: v for k, v in und.settings.model_dump().items()
                   if v is not None and k in DEFAULT_SETTINGS}  # characters and streaming are handled below
        # "only grandma" arrives as on=[grandma], off=[everyone else]; unknown ids are ignored
        for cid in und.settings.characters_off:
            if cid in persona.CHARACTERS:
                changes[persona.setting(cid)] = False
        for cid in und.settings.characters_on:
            if cid in persona.CHARACTERS:
                changes[persona.setting(cid)] = True
        applied = {}
        s = und.settings
        if s.streaming_set or s.streaming_add or s.streaming_remove:
            applied["streaming"] = await self._set_streaming(s)
        for key, value in changes.items():
            if key in SETTING_LIMITS:
                lo, hi = SETTING_LIMITS[key]
                value = max(lo, min(hi, int(value)))
            self.db.set_setting(key, value)
            applied[key] = value
        if not applied:
            await self._say_reply(msg, t("sit.settings_unclear"), self.db.settings())
            return
        log.info(t("log.settings_changed", changes=applied))
        # Speaker is chosen after applying, so "kein Pirat mehr" is already confirmed without him.
        await self._say_reply(msg, t("sit.settings_changed"),
                              {"changed": applied, "all_settings": self.db.settings()})

    async def _set_streaming(self, s) -> dict:
        """"We have Netflix and Disney+" / "we cancelled Prime": change STREAMING_PROVIDERS like the web
        interface does (saved in config.json, applies to the next cards right away)."""
        providers = await self.seerr.watch_providers(self.cfg.streaming_region)
        ids = set(self.cfg.streaming_ids)
        missing: list[str] = []
        if s.streaming_set:
            found, miss = self.seerr.match_providers(s.streaming_set, providers)
            ids, missing = {p["id"] for p in found}, missing + miss
        found, miss = self.seerr.match_providers(s.streaming_add, providers)
        ids |= {p["id"] for p in found}
        missing += miss
        found, miss = self.seerr.match_providers(s.streaming_remove, providers)
        ids -= {p["id"] for p in found}
        missing += miss
        overrides = config.read_overrides(self.cfg.data_dir)
        overrides["STREAMING_PROVIDERS"] = ",".join(str(i) for i in sorted(ids))
        config.write_overrides(self.cfg.data_dir, overrides)
        self.on_config_change()
        names = [p["name"] for p in providers if p["id"] in ids]
        log.info(t("log.streaming_changed", services=", ".join(names) or "–"))
        return {"streaming_services_now": names, "not_found": missing, "country": self.cfg.streaming_region}

    async def _chat(self, msg: Message, text: str) -> None:
        await self._say_reply(msg, t("sit.chat"), {"message": text})

    # --- feedback ----------------------------------------------------------------
    @in_chat_language
    async def ask_feedback(self, req: dict) -> bool:
        """Notifier for FeedbackService: post a question for a pending request."""
        if self.down or self.app is None:
            return False
        speaker = self.speaker()
        settings = self.db.settings()
        title = req["title"]
        if req["kind"] == "movie":
            situation = t("sit.ask_movie", title=title)
        elif req["kind"] == "season":
            season = req["extra"].get("season")
            if req["extra"].get("series_done"):
                what = t("sit.whole_series")
            else:  # Jellyfin may not know the season number (specials, odd metadata)
                what = t("sit.season_n", season=season) if season is not None else t("sit.a_season")
            situation = t("sit.ask_season", what=what, title=title)
        elif req["kind"] == "series_pause":
            situation = t("sit.ask_pause", title=title, days=settings["series_pause_days"])
        else:
            pct = round(100 * req["extra"].get("pct", 0))
            situation = t("sit.ask_abort", title=title, pct=pct, days=settings["abort_days"])
        situation += t("sit.ask_suffix")
        try:
            text = await self.llm.say(situation, speaker)
        except LLMUnavailable as e:
            await self.enter_outage(str(e))
            return False
        except LLMFailed:
            return False

        rid = req["id"]
        if req["kind"] == "abort":
            rows = [[InlineKeyboardButton(t("bot.btn_abort_bad"), callback_data=f"ab:{rid}:bad")],
                    [InlineKeyboardButton(t("bot.btn_abort_tired"), callback_data=f"ab:{rid}:tired")],
                    [InlineKeyboardButton(t("bot.btn_abort_later"), callback_data=f"ab:{rid}:later")]]
        else:
            rows = [[InlineKeyboardButton(t("bot.btn_up"), callback_data=f"fb:{rid}:up"),
                     InlineKeyboardButton(t("bot.btn_meh"), callback_data=f"fb:{rid}:meh"),
                     InlineKeyboardButton(t("bot.btn_down"), callback_data=f"fb:{rid}:down")]]
        # The title is set by the code, not left to the model – the question must always say what it is about.
        body = f"{self._feedback_header(req)}\n\n{html.escape(text, quote=False)}"
        sent = await self.post(body, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        self.db.update_request(rid, status="sent", sent_at=now(), message_id=sent.message_id)
        log.info(t("log.feedback_asked", title=title, kind=req["kind"]))
        return True

    @staticmethod
    def _feedback_header(req: dict) -> str:
        title = f"<b>{html.escape(req['title'])}</b>"
        extra = req["extra"]
        if req["kind"] == "movie":
            return t("bot.head_movie", title=title)
        if req["kind"] == "season":
            if extra.get("series_done"):
                return t("bot.head_series_done", title=title)
            if extra.get("season") is None:
                return t("bot.head_series", title=title)
            return t("bot.head_season", title=title, season=extra["season"])
        if req["kind"] == "series_pause":
            return t("bot.head_pause", title=title)
        return t("bot.head_abort", title=title, pct=round(100 * extra.get("pct", 0)))

    async def _feedback_text(self, msg: Message, req: dict, text: str) -> None:
        """Free text as a reply to a feedback question."""
        und = await self.llm.understand(t("bot.feedback_reply_prefix", title=req["title"], text=text), [req])
        await self._adopt_language(und)
        await self._apply_feedback_text(msg, req, text, und.feedback.rating, und.feedback.text or text)

    async def _feedback_intent(self, msg: Message, text: str, und: Understanding) -> None:
        recent = self.db.recent_requests()
        req = next((r for r in recent if r["title_key"] == und.feedback.title_key), None)
        if req is None and len(recent) == 1:
            req = recent[0]
        if req is None:
            await self._say_reply(msg, t("sit.feedback_unclear"), {"recent": [r["title"] for r in recent]})
            return
        await self._apply_feedback_text(msg, req, text, und.feedback.rating, und.feedback.text or text)

    async def _apply_feedback_text(self, msg: Message, req: dict, text: str, rating: str,
                                   core: str) -> None:
        rater = self._rater(msg.from_user)
        rated = req["extra"].get("ratings") or {}
        fb_id = (rated.get(str(rater[0])) or {}).get("feedback_id") if rater else req["extra"].get("feedback_id")
        if fb_id is None and rating in ("up", "meh", "down"):
            fb_id = await self.feedback.store_rating(req, rating, free_text=text, rater=rater)
        if req["status"] in ("sent", "pending") and (req["kind"] == "abort" or self._all_rated(req)):
            # Any answer counts – an answered abort question never turns into the light thumbs down.
            self.db.update_request(req["id"], status="answered")
        genres, keywords = await self.feedback.title_tags(req)
        res = await self.llm.feedback_traits(req["title"], rating, core, genres, keywords, self.speaker())
        for tr in res.traits:
            self.db.add_trait(tr.direction, tr.trait, tr.tmdb_keywords, fb_id, rater=rater)
            log.info(t("log.trait_saved", direction=tr.direction, trait=tr.trait, keywords=tr.tmdb_keywords))
        self.feedback.profiles.invalidate()
        await msg.reply_text(res.reply)

    # --- buttons -------------------------------------------------------------------
    @in_chat_language
    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not q.message or q.message.chat_id != self.cfg.chat_id or not q.data:
            return
        self._note_member(q.from_user)
        parts = q.data.split(":")
        try:
            if parts[0] == "req":
                await self._cb_request(q, int(parts[1]))
            elif parts[0] == "rej":
                await self._cb_reject(q, int(parts[1]))
            elif parts[0] == "acc":
                await self._cb_accept(q, int(parts[1]))
            elif parts[0] == "col":
                await self._cb_collection(q, int(parts[1]))
            elif parts[0] == "nav":
                await self._cb_nav(q, int(parts[1]), int(parts[2]))
            elif parts[0] == "fb":
                await self._cb_rating(q, int(parts[1]), parts[2])
            elif parts[0] == "ab":
                await self._cb_abort(q, int(parts[1]), parts[2])
            else:
                await q.answer()
        except LLMUnavailable as e:
            await self.enter_outage(str(e))
        except LLMFailed as e:  # the button did its job – only the character's comment is missing
            log.warning(t("log.llm_failed", provider=self.llm.label, error=e))

    async def _show_ratings(self, q, req: dict, keep_buttons: bool) -> None:
        """One line "🗳️ 👍 Sam · 👎 Alex" under the question, updated with every rating; the buttons stay
        until everyone has rated."""
        m = q.message
        ratings = req["extra"].get("ratings") or {}
        line = RATINGS_MARK + " " + " · ".join(f"{RATING_EMOJI[r['rating']]} {html.escape(r['name'])}"
                                                for r in ratings.values())
        text = "\n".join(ln for ln in m.text_html.split("\n") if not ln.startswith(RATINGS_MARK)).rstrip()
        markup = m.reply_markup if keep_buttons else None
        try:
            await m.edit_text(f"{text}\n\n{line}", parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError as e:
            log.warning(t("log.not_editable", error=e))

    async def _mark(self, q, note: str, keep: list[list[InlineKeyboardButton]] | None = None) -> None:
        """Append a status line to the message and replace its buttons (link buttons like the trailer stay)."""
        m = q.message
        if keep is None:
            old = m.reply_markup.inline_keyboard if getattr(m, "reply_markup", None) else ()
            keep = [row for row in ([b for b in r if b.url] for r in old) if row]
        markup = InlineKeyboardMarkup(keep) if keep else None
        try:
            if m.photo:
                await m.edit_caption(caption=f"{m.caption_html}\n\n{note}", parse_mode=ParseMode.HTML,
                                     reply_markup=markup)
            else:
                await m.edit_text(f"{m.text_html}\n\n{note}", parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError as e:
            log.warning(t("log.not_editable", error=e))

    async def _cb_request(self, q, sid: int) -> None:
        s = self.db.suggestion(sid)
        if not s or s["source"] != "new" or not s["tmdb_id"]:
            await q.answer(t("bot.not_requestable"))
            return
        if s["status"] == "requested":
            await q.answer(t("bot.already_requested"))
            return
        who = self._who(q)
        # Mark first: both people may press the button at the same time.
        self.db.set_suggestion_status(sid, "requested", who)
        await q.answer()  # right away – the request takes a moment; the result goes into the card
        try:
            await self.seerr.request(s["media_type"], s["tmdb_id"])
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 409:  # 409: already requested in Jellyseerr itself – on its way all the same
                self.db.set_suggestion_status(sid, s["status"])
                log_request_failed(s["title"], e)
                await self._post_failure(s["title"], t("bot.request_failed"))
                return
        except httpx.HTTPError:
            self.db.set_suggestion_status(sid, s["status"])
            await self._post_failure(s["title"], t("bot.seerr_unreachable"))
            return
        log.info(t("log.requested", title=s["title"], who=who))
        await self._decided(q, sid, t("bot.requested_by", who=html.escape(who)))
        speaker = self.speaker()
        text = await self.llm.say(
            t("sit.requested"), speaker,
            {"title": s["title"], "type": s["media_type"], "requested_by": who})
        # Not as a reply: Telegram quotes the card as it looks *now*, so after paging on the quote would
        # show another title. The message names the title itself.
        await self.post(text)

    async def _post_failure(self, title: str, text: str) -> None:
        """A button's failure as a message naming the title (the button was answered already, and the card
        may show another page by now)."""
        try:
            await self.post(f"<b>{html.escape(title)}</b> – {text}", parse_mode=ParseMode.HTML)
        except TelegramError as e:
            log.warning(t("log.not_editable", error=e))

    async def _cb_accept(self, q, sid: int) -> None:
        s = self.db.suggestion(sid)
        if not s:
            await q.answer()
            return
        if s["status"] == "accepted":
            await q.answer(t("bot.already_accepted"))
            return
        who = self._who(q)
        self.db.set_suggestion_status(sid, "accepted", who)
        log.info(t("log.accepted", title=s["title"], who=who))
        await q.answer(t("bot.accepted_answer"))
        await self._decided(q, sid, t("bot.accepted_note", who=html.escape(who)))

    async def _cb_reject(self, q, sid: int) -> None:
        s = self.db.suggestion(sid)
        if not s:
            await q.answer()
            return
        who = self._who(q)
        self.db.reject(s["title_key"], s["title"])
        self.db.set_suggestion_status(sid, "rejected", who)
        log.info(t("log.rejected", title=s["title"]))
        await q.answer(t("bot.rejected_answer"))
        await self._decided(q, sid, t("bot.rejected_note"))

    async def _cb_rating(self, q, rid: int, rating: str) -> None:
        req = self.db.request(rid)
        if not req or req["status"] not in ("sent", "pending") or rating not in RATING_EMOJI:
            await q.answer(t("bot.already_answered"))
            return
        await q.answer()  # right away – looking up the title's tags takes a moment; the rating shows in the message
        rater = self._rater(q.from_user)
        await self.feedback.store_rating(req, rating, rater=rater)
        req = self.db.request(rid)  # with everyone's ratings – someone else may have rated meanwhile
        done = self._all_rated(req)
        if done:
            self.db.update_request(rid, status="answered")
        label = t(f"bot.btn_{rating}")
        log.info(t("log.rated", title=req["title"], rating=f"{label} ({rater[1] if rater else '?'})"))
        await self._show_ratings(q, req, keep_buttons=not done)

    async def _cb_abort(self, q, rid: int, choice: str) -> None:
        req = self.db.request(rid)
        if not req or req["status"] not in ("sent", "pending"):
            await q.answer(t("bot.already_answered"))
            return
        if choice == "bad":
            await self.feedback.store_rating(req, "down", rater=self._rater(q.from_user))
            self.db.update_request(rid, status="answered")
            note = t("bot.abort_bad_note")
        elif choice == "tired":
            self.db.update_request(rid, status="answered")
            note = t("bot.abort_tired_note")
        else:
            due = now() + timedelta(days=self.db.settings()["abort_days"])
            self.db.update_request(rid, status="scheduled", due_at=due, sent_at=None)
            note = t("bot.abort_later_note")
        log.info(t("log.rated", title=req["title"], rating=note))
        await q.answer(note)
        await self._mark(q, note)
