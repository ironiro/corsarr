"""Telegram side: mentions/replies, suggestion cards, buttons, feedback questions, outage mode."""
from __future__ import annotations

import asyncio
import functools
import html
import json
import logging
import re
import time
from dataclasses import asdict
from datetime import timedelta

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message, MessageEntity, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, ContextTypes

from . import arr, persona, usage
from .i18n import default_language, language, normalize, switch_language, t, use_language
from .monitor import health
from .config import Config
from .db import DB, iso, now
from .feedback import FeedbackService
from .jellyfin import Jellyfin
from .jellyseerr import Jellyseerr
from .llm import LLM, LLMFailed, LLMUnavailable, Understanding
from .translate import Translations
from .models import Candidate, FilmCollection
from .poster import placeholder_png
from .recommender import Recommender

log = logging.getLogger(__name__)

SETTING_LIMITS = {"series_pause_days": (1, 365), "abort_days": (1, 60)}
CAPTION_LIMIT = 1024


def in_chat_language(method):
    """Run a handler in the group's language – for everything the bot does without a fresh message."""
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        with use_language(self.chat_language()):
            return await method(self, *args, **kwargs)
    return wrapper



RATINGS_MARK = "🗳️"
RATING_EMOJI = {"up": "👍", "meh": "😐", "down": "👎"}

class CorsarrBot:
    def __init__(self, cfg: Config, db: DB, jellyfin: Jellyfin, seerr: Jellyseerr, llm: LLM,
                 recommender: Recommender, feedback: FeedbackService):
        self.cfg, self.db, self.jellyfin, self.seerr = cfg, db, jellyfin, seerr
        self.llm, self.recommender, self.feedback = llm, recommender, feedback
        self.translations = Translations(db, llm)  # fixed texts for languages without built-in ones
        self.app: Application | None = None
        self.bot_id: int | None = None
        self.username = ""
        self.down = False
        self._last_outage_reply = 0.0
        self.genres_loaded = False

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
        known = [c for c in known if c["id"] != msg.chat_id][-4:]  # the last five people
        known.append({"id": msg.chat_id, "name": user.full_name or user.username or str(user.id), "at": iso(now())})
        self.db.set_state("private_chats", json.dumps(known, ensure_ascii=False))
        log.info(t("log.private_chat", name=user.full_name, id=msg.chat_id))
        with use_language(user.language_code or self.chat_language()):
            await msg.reply_text(t("bot.private_hello", name=html.escape(user.first_name or ""), id=msg.chat_id),
                                 parse_mode=ParseMode.HTML)

    # --- the people in the group (for ratings per person) -------------------------
    def _note_member(self, user) -> None:
        """Remember who is in the group – everyone who writes to the bot or presses a button."""
        if not user or getattr(user, "is_bot", False):
            return
        members = json.loads(self.db.get_state("members") or "{}")
        name = user.first_name or user.full_name or str(user.id)
        if members.get(str(user.id), {}).get("name") != name or not members.get(str(user.id)):
            members[str(user.id)] = {"name": name, "seen": iso(now())}
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
        if self.down and not await self._probe():
            if time.monotonic() - self._last_outage_reply > 1800:
                self._last_outage_reply = time.monotonic()
                await msg.reply_text(t("bot.outage"))
            return
        await self.load_genres()
        await context.bot.send_chat_action(self.cfg.chat_id, ChatAction.TYPING)
        try:
            reply_to = msg.reply_to_message
            fb_req = self.db.request_by_message(reply_to.message_id) if reply_to else None
            if fb_req:
                await self._feedback_text(msg, fb_req, text)
                return
            und = await self.llm.understand(text or t("bot.hello"), self.db.recent_requests())
            await self._adopt_language(und)
            who = msg.from_user.first_name if msg.from_user else "?"
            short = text if len(text) <= 120 else text[:119] + "…"
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

    def _caption(self, c: Candidate, reason: str, note: str = "") -> str:
        kind = t("bot.movie") if c.media_type == "movie" else t("bot.series")
        lines = [f"<b>{html.escape(c.label)}</b>"]
        if c.source == "library":
            lines.append(t("bot.in_library", kind=kind))
        elif c.source == "pending":
            lines.append(t("bot.pending", kind=kind))
        else:
            lines.append(t("bot.not_available", kind=kind))
        facts = []
        if c.rating:
            facts.append(t("bot.rating", rating=f"{float(c.rating):.1f}"))
        if c.runtime_min:
            per = t("bot.per_episode") if c.media_type == "tv" else ""
            facts.append(t("bot.runtime", minutes=c.runtime_min) + per)
        if c.seasons:
            facts.append(t("bot.season_one") if c.seasons == 1 else t("bot.season_many", n=c.seasons))
        if facts:
            lines.append(" · ".join(facts))
        if c.source == "new" and not note:
            lines.append(t("bot.download_hint"))
        tail = f"\n\n{html.escape(reason)}" if reason else ""
        tail += f"\n\n<b>{note}</b>" if note else ""
        head = "\n".join(lines)
        room = CAPTION_LIMIT - len(head) - len(tail) - 4
        overview = c.overview if len(c.overview) <= room else c.overview[: max(room - 1, 0)].rsplit(" ", 1)[0] + "…"
        return f"{head}\n\n{html.escape(overview)}{tail}" if overview else f"{head}{tail}"

    # --- browsable card: one photo message, ◀️ ▶️ switch between the suggestions ---------------
    async def _send_carousel(self, picks: list[tuple[Candidate, str]]) -> None:
        pages = []
        for cand, reason in picks:
            sid = self.db.add_suggestion(cand)
            card = {"candidate": asdict(cand), "reason": reason, "lang": language()}
            pages.append((sid, json.dumps(card, ensure_ascii=False)))
            log.info(t("log.card", title=cand.label, source=cand.source, reason=reason or "–"))
        cid = self.db.create_carousel(pages)
        row, photo, caption, markup = await self._page(cid, 0)
        try:
            sent = await self.bot.send_photo(self.cfg.chat_id, photo, caption=caption,
                                             parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError as e:  # e.g. Telegram could not fetch the poster URL
            log.warning(t("log.card_failed", title=row["title"], error=e))
            sent = await self.bot.send_photo(self.cfg.chat_id, placeholder_png(), caption=caption,
                                             parse_mode=ParseMode.HTML, reply_markup=markup)
        self.db.set_carousel(cid, message_id=sent.message_id)
        self._remember_photo(row["id"], sent)

    async def _page(self, cid: int, pos: int):
        """Everything needed to show page `pos`: (suggestion row, photo, caption, buttons)."""
        rows = self.db.carousel_pages(cid)
        pos %= len(rows)
        row = rows[pos]
        card = json.loads(row["card"])
        with use_language(card.get("lang")):  # a card stays in the language it was sent in
            return await self._render_page(cid, pos, rows, row, card)

    async def _render_page(self, cid: int, pos: int, rows, row, card: dict):
        cand = Candidate(**card["candidate"])
        caption = self._caption(cand, card["reason"], self._note(row))

        buttons = []
        if row["status"] == "suggested":
            if cand.source == "new":
                buttons.append(InlineKeyboardButton(t("bot.btn_request"), callback_data=f"req:{row['id']}"))
            elif cand.source == "library":  # for new titles "request" already is the yes; pending: on its way
                buttons.append(InlineKeyboardButton(t("bot.btn_accept"), callback_data=f"acc:{row['id']}"))
            buttons.append(InlineKeyboardButton(t("bot.btn_reject"), callback_data=f"rej:{row['id']}"))
        keyboard = [buttons] if buttons else []
        if len(rows) > 1:
            keyboard.append([
                InlineKeyboardButton("◀️", callback_data=f"nav:{cid}:{(pos - 1) % len(rows)}"),
                InlineKeyboardButton(f"{pos + 1} / {len(rows)}", callback_data="noop"),
                InlineKeyboardButton("▶️", callback_data=f"nav:{cid}:{(pos + 1) % len(rows)}"),
            ])
        if cand.trailer_url and cand.trailer_url.startswith("https://"):
            keyboard.append([InlineKeyboardButton(t("bot.btn_trailer"), url=cand.trailer_url)])
        return row, await self._photo(row, cand), caption, InlineKeyboardMarkup(keyboard)

    async def _photo(self, row, cand: Candidate):
        if row["file_id"]:
            return row["file_id"]  # already uploaded once – no new download
        if cand.source == "library" and cand.jellyfin_id:
            return await self.jellyfin.poster(cand.jellyfin_id) or placeholder_png()
        return cand.poster_url or placeholder_png()

    def _remember_photo(self, sid: int, message) -> None:
        photo = getattr(message, "photo", None)  # edit_media may also return True instead of a message
        if photo:
            self.db.set_suggestion_file_id(sid, photo[-1].file_id)

    @staticmethod
    def _note(row) -> str:
        who = html.escape(row["decided_by"] or t("bot.someone"))
        return {"accepted": t("bot.accepted_note", who=who), "rejected": t("bot.rejected_note"),
                "requested": t("bot.requested_by", who=who)}.get(row["status"], "")

    async def _cb_nav(self, q, cid: int, pos: int) -> None:
        if self.db.carousel(cid) is None:
            await q.answer()
            return
        row, photo, caption, markup = await self._page(cid, pos)
        await q.answer()
        try:
            edited = await q.message.edit_media(
                InputMediaPhoto(photo, caption=caption, parse_mode=ParseMode.HTML), reply_markup=markup)
        except TelegramError as e:
            if "not modified" in str(e).lower():
                return  # both pressed at once – already showing this page
            log.warning(t("log.not_editable", error=e))
            edited = await q.message.edit_media(
                InputMediaPhoto(placeholder_png(), caption=caption, parse_mode=ParseMode.HTML), reply_markup=markup)
        self.db.set_carousel(cid, position=pos % len(self.db.carousel_pages(cid)))
        self._remember_photo(row["id"], edited)

    async def _decided(self, q, sid: int, note: str) -> None:
        """Show a decision: redraw the card's current page, or append a note on older single cards."""
        s = self.db.suggestion(sid)
        car = self.db.carousel(s["carousel_id"]) if s and s["carousel_id"] else None
        if car is None:
            await self._mark(q, note)
            return
        ids = [r["id"] for r in self.db.carousel_pages(car["id"])]
        _, _, caption, markup = await self._page(car["id"], ids.index(sid))
        try:
            await q.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError as e:
            log.warning(t("log.not_editable", error=e))

    # --- film series: one message listing all parts, one button requests the missing ones ----------
    async def _send_collection(self, coll: FilmCollection, intro: str) -> None:
        parts = []
        for c in coll.parts:
            # Missing parts get a suggestion row: it records who requested them (and was_requested sees it).
            sid = self.db.add_suggestion(c) if c.source == "new" and c.tmdb_id else None
            parts.append({"label": c.label, "source": c.source, "sid": sid})
        card = {"name": coll.name, "poster_url": coll.poster_url, "intro": intro, "lang": language(),
                "parts": parts}
        col_id = self.db.add_collection(coll.tmdb_id, json.dumps(card, ensure_ascii=False))
        caption, markup = self._collection_view(col_id, card)
        try:
            sent = await self.bot.send_photo(self.cfg.chat_id, coll.poster_url or placeholder_png(),
                                             caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError as e:  # e.g. Telegram could not fetch the poster URL
            log.warning(t("log.card_failed", title=coll.name, error=e))
            sent = await self.bot.send_photo(self.cfg.chat_id, placeholder_png(), caption=caption,
                                             parse_mode=ParseMode.HTML, reply_markup=markup)
        self.db.set_collection_message(col_id, sent.message_id)

    def _missing_parts(self, card: dict) -> list:
        """Suggestion rows of the parts that are still missing (not requested via the bot yet)."""
        rows = [self.db.suggestion(p["sid"]) for p in card["parts"] if p.get("sid")]
        return [r for r in rows if r is not None and r["status"] != "requested"]

    def _collection_view(self, col_id: int, card: dict) -> tuple[str, InlineKeyboardMarkup | None]:
        with use_language(card.get("lang")):  # the message stays in the language it was sent in
            lines = []
            for p in card["parts"]:
                title = html.escape(p["label"])
                row = self.db.suggestion(p["sid"]) if p.get("sid") else None
                if row is not None and row["status"] == "requested":
                    lines.append(t("bot.coll_requested_by", title=title,
                                   who=html.escape(row["decided_by"] or t("bot.someone"))))
                else:
                    key = {"library": "bot.coll_library", "pending": "bot.coll_pending",
                           "blocked": "bot.coll_blocked"}.get(p["source"], "bot.coll_missing")
                    lines.append(t(key, title=title))
            head = f"<b>{html.escape(card['name'])}</b>"
            intro = html.escape(card.get("intro") or "")
            caption = "\n".join([head, *lines])
            if intro and len(intro) + len(caption) + 2 <= CAPTION_LIMIT:
                caption = f"{intro}\n\n{caption}"
            while len(caption) > CAPTION_LIMIT and lines:  # very long series: cut the list
                lines.pop()
                more = t("bot.coll_more", n=len(card["parts"]) - len(lines))
                caption = "\n".join([head, *lines, more])
            missing = len(self._missing_parts(card))
            if not missing:
                return caption, None
            button = InlineKeyboardButton(t("bot.btn_request_missing", n=missing), callback_data=f"col:{col_id}")
            return caption, InlineKeyboardMarkup([[button]])

    async def _cb_collection(self, q, col_id: int) -> None:
        col = self.db.collection(col_id)
        if col is None:
            await q.answer()
            return
        card = json.loads(col["card"])
        missing = self._missing_parts(card)
        if not missing:
            await q.answer(t("bot.coll_nothing_missing"))
            return
        who = q.from_user.first_name if q.from_user else t("bot.someone")
        # Mark first: both people may press the button at the same time.
        for s in missing:
            self.db.set_suggestion_status(s["id"], "requested", who)
        results = await asyncio.gather(*(self.seerr.request("movie", s["tmdb_id"]) for s in missing),
                                       return_exceptions=True)
        requested, failed = [], 0
        for s, res in zip(missing, results):
            if isinstance(res, httpx.HTTPStatusError) and res.response.status_code == 409:
                requested.append(s)  # already requested in Jellyseerr itself – on its way all the same
            elif isinstance(res, BaseException):
                self.db.set_suggestion_status(s["id"], s["status"])
                failed += 1
                if isinstance(res, httpx.HTTPStatusError):
                    log.warning(t("log.request_failed", title=s["title"], status=res.response.status_code,
                                  body=res.response.text))
                else:
                    log.warning(t("log.request_failed", title=s["title"], status="-", body=res))
            else:
                requested.append(s)
                log.info(t("log.requested", title=s["title"], who=who))
        if failed:
            await q.answer(t("bot.coll_failed", n=failed), show_alert=True)
        else:
            await q.answer(t("bot.coll_requested", n=len(requested)))
        caption, markup = self._collection_view(col_id, card)
        try:
            await q.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError as e:
            log.warning(t("log.not_editable", error=e))
        if not requested:
            return
        try:
            text = await self.llm.say(t("sit.collection_requested"), self.speaker(), {
                "series": card["name"], "titles": [s["title"] for s in requested], "requested_by": who})
        except LLMFailed:
            return
        await self.post(text, reply_to_message_id=q.message.message_id)

    # --- settings --------------------------------------------------------------
    async def _settings(self, msg: Message, und: Understanding) -> None:
        changes = {k: v for k, v in und.settings.model_dump().items()
                   if v is not None and k not in ("characters_on", "characters_off")}
        # "only grandma" arrives as on=[grandma], off=[everyone else]; unknown ids are ignored
        for cid in und.settings.characters_off:
            if cid in persona.CHARACTERS:
                changes[persona.setting(cid)] = False
        for cid in und.settings.characters_on:
            if cid in persona.CHARACTERS:
                changes[persona.setting(cid)] = True
        applied = {}
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
            what = (t("sit.whole_series") if req["extra"].get("series_done")
                    else t("sit.season_n", season=season))
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
            return t("bot.head_season", title=title, season=extra.get("season"))
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
        who = q.from_user.first_name if q.from_user else t("bot.someone")
        # Mark first: both people may press the button at the same time.
        self.db.set_suggestion_status(sid, "requested", who)
        try:
            await self.seerr.request(s["media_type"], s["tmdb_id"])
        except httpx.HTTPError as e:
            self.db.set_suggestion_status(sid, s["status"])
            if isinstance(e, httpx.HTTPStatusError):
                log.warning(t("log.request_failed", title=s["title"], status=e.response.status_code,
                              body=e.response.text))
                await q.answer(t("bot.request_failed"), show_alert=True)
            else:
                await q.answer(t("bot.seerr_unreachable"), show_alert=True)
            return
        log.info(t("log.requested", title=s["title"], who=who))
        await q.answer(t("bot.requested"))
        await self._decided(q, sid, t("bot.requested_by", who=html.escape(who)))
        speaker = self.speaker()
        text = await self.llm.say(
            t("sit.requested"), speaker,
            {"title": s["title"], "type": s["media_type"], "requested_by": who})
        await self.post(text, reply_to_message_id=q.message.message_id)

    async def _cb_accept(self, q, sid: int) -> None:
        s = self.db.suggestion(sid)
        if not s:
            await q.answer()
            return
        if s["status"] == "accepted":
            await q.answer(t("bot.already_accepted"))
            return
        who = q.from_user.first_name if q.from_user else t("bot.someone")
        self.db.set_suggestion_status(sid, "accepted", who)
        log.info(t("log.accepted", title=s["title"], who=who))
        await q.answer(t("bot.accepted_answer"))
        await self._decided(q, sid, t("bot.accepted_note", who=html.escape(who)))

    async def _cb_reject(self, q, sid: int) -> None:
        s = self.db.suggestion(sid)
        if not s:
            await q.answer()
            return
        who = q.from_user.first_name if q.from_user else t("bot.someone")
        self.db.reject(s["title_key"], s["title"])
        self.db.set_suggestion_status(sid, "rejected", who)
        log.info(t("log.rejected", title=s["title"]))
        await q.answer(t("bot.rejected_answer"))
        await self._decided(q, sid, t("bot.rejected_note"))

    async def _cb_rating(self, q, rid: int, rating: str) -> None:
        req = self.db.request(rid)
        if not req or req["status"] not in ("sent", "pending"):
            await q.answer(t("bot.already_answered"))
            return
        rater = self._rater(q.from_user)
        await self.feedback.store_rating(req, rating, rater=rater)
        done = self._all_rated(req)
        if done:
            self.db.update_request(rid, status="answered")
        label = t(f"bot.btn_{rating}")
        log.info(t("log.rated", title=req["title"], rating=f"{label} ({rater[1] if rater else '?'})"))
        await q.answer(t("bot.saved", label=label))
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

    # --- jobs ----------------------------------------------------------------------
    async def _probe(self) -> bool:
        try:
            await self.llm.ping()
        except (LLMUnavailable, LLMFailed):
            return False
        self.down = False
        log.info(t("log.llm_back", provider=self.llm.label))
        await self.post(t("bot.recovered"))
        return True

    @in_chat_language
    async def job_outage(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.down:
            await self._probe()

    @in_chat_language
    async def job_downloads(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send bundled Sonarr/Radarr download messages whose imports have settled."""
        for ids, text in arr.due_messages(self.db):
            try:
                await self.bot.send_message(self.cfg.notify_chat_id, text)
            except TelegramError as e:
                log.warning(t("log.notify_failed", error=e))
                return  # keep them pending, retry next run
            self.db.mark_imports_notified(ids)
            log.info(t("log.notified", text=text))

    @in_chat_language
    async def job_budget(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Warn once per month at 80 % of the budget and once when it is used up – privately to the admin
        if ADMIN_CHAT_ID is set, else in the group."""
        budget = usage.budget_usd(self.cfg)
        cost = usage.month_cost(self.db) if budget else None
        if not budget or cost is None:
            return
        month = usage.month_start().strftime("%Y-%m")
        warned = self.db.get_state(f"budget_warned:{month}") or ""
        if cost >= budget and warned != "100":
            text, level = t("bot.budget_reached", cost=f"{cost:.2f}", budget=f"{budget:.2f}"), "100"
        elif cost >= usage.WARN_SHARE * budget and not warned:
            text, level = t("bot.budget_warning", cost=f"{cost:.2f}", budget=f"{budget:.2f}"), "80"
        else:
            return
        target = int(self.cfg.get("ADMIN_CHAT_ID") or 0) or self.cfg.chat_id
        try:
            await self.bot.send_message(target, text)
        except TelegramError as e:
            log.warning(t("log.notify_failed", error=e))
            return
        self.db.set_state(f"budget_warned:{month}", level)
        log.info(t("log.budget_warned", level=level, cost=f"{cost:.2f}", budget=f"{budget:.2f}"))

    async def job_catch_up(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Imports whose webhook never arrived (Corsarr offline, network) – only with remembered access."""
        for kind in ("sonarr", "radarr"):
            url, key = self.cfg.get(f"{kind.upper()}_URL"), self.cfg.get(f"{kind.upper()}_API_KEY")
            if not (url and key):
                continue
            try:
                added = await arr.catch_up(self.db, kind, url, key)
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                log.warning(t("log.catch_up_failed", service=kind.capitalize(), error=e))
                continue
            if added:
                log.info(t("log.catch_up", service=kind.capitalize(), n=added))

    @in_chat_language
    async def job_feedback(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if self.down:
            return
        await self.load_genres()
        try:
            await self.feedback.tick()
        except Exception:
            log.exception(t("log.feedback_job_failed"))
