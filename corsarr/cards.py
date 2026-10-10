"""Suggestion cards in Telegram: captions, the browsable carousel (one photo message, ◀️ ▶️ pages) and
the film-series message. Mixed into CorsarrBot (bot.py), which provides db, jellyfin, seerr, llm, cfg,
bot, post(), speaker() and _mark()."""
from __future__ import annotations

import asyncio
import html
import json
import logging
from dataclasses import asdict

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import TelegramError

from .i18n import language, t, use_language
from .llm import LLMFailed
from .models import Candidate, FilmCollection
from .poster import placeholder_png

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024  # Telegram's limit for photo captions


def log_request_failed(title: str, error: BaseException) -> None:
    """A Jellyseerr request that failed – with the HTTP status and body when there is one."""
    if isinstance(error, httpx.HTTPStatusError):
        log.warning(t("log.request_failed", title=title, status=error.response.status_code,
                      body=error.response.text))
    else:
        log.warning(t("log.request_failed", title=title, status="-", body=error))


class CardsMixin:
    _card_locks: dict[int, asyncio.Lock]  # per carousel: one message edit at a time
    _nav_wanted: dict[int, int]           # per carousel: the page the newest tap asked for

    @staticmethod
    def _who(q) -> str:
        """First name of whoever pressed a button."""
        return q.from_user.first_name if q.from_user else t("bot.someone")

    def _caption(self, c: Candidate, reason: str, note: str = "") -> str:
        kind = t("bot.movie") if c.media_type == "movie" else t("bot.series")
        lines = [f"<b>{html.escape(c.label)}</b>"]
        if c.source == "library":
            lines.append(t("bot.in_library", kind=kind))
        elif c.source == "pending":
            lines.append(t("bot.pending", kind=kind))
        else:
            lines.append(t("bot.not_available", kind=kind))
            if c.streaming:  # on a service they already pay for – they may rather watch it there
                lines.append(t("bot.streaming", providers=html.escape(", ".join(c.streaming[:4]))))
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

    async def _send_photo_or_placeholder(self, photo, caption: str, markup, title: str) -> tuple[object, bool]:
        """Send a photo message – with the stand-in image when Telegram can't fetch the poster.
        Returns (sent message, whether the real poster was used)."""
        try:
            sent = await self.bot.send_photo(self.cfg.chat_id, photo, caption=caption,
                                             parse_mode=ParseMode.HTML, reply_markup=markup)
            return sent, True
        except TelegramError as e:  # e.g. Telegram could not fetch the poster URL
            log.warning(t("log.card_failed", title=title, error=e))
        sent = await self.bot.send_photo(self.cfg.chat_id, placeholder_png(), caption=caption,
                                         parse_mode=ParseMode.HTML, reply_markup=markup)
        return sent, False

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
        sent, real = await self._send_photo_or_placeholder(photo, caption, markup, row["title"])
        if real:  # the stand-in is not remembered as the poster: the next page change tries the real one again
            self._remember_photo(row["id"], sent)
        self.db.set_carousel(cid, message_id=sent.message_id)

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

    def _card_lock(self, cid: int) -> asyncio.Lock:
        """One edit per card at a time: Telegram cancels an edit when the next one for the same message
        arrives before it finished ("Canceled by new edit message request")."""
        return self._card_locks.setdefault(cid, asyncio.Lock())

    async def _cb_nav(self, q, cid: int, pos: int) -> None:
        await q.answer()  # right away – fetching the poster takes a moment, and a spinning button invites taps
        if self.db.carousel(cid) is None:
            return
        self._nav_wanted[cid] = pos
        await self._show_wanted(q.message, cid)

    async def _show_wanted(self, message, cid: int) -> None:
        """Show the page the newest tap asked for – unless someone else holds the card's lock: whoever has it
        (a page change, a decision) shows the wish when done, so a tap during an edit is never lost."""
        lock = self._card_lock(cid)
        if lock.locked():
            return
        async with lock:
            while (target := self._nav_wanted.pop(cid, None)) is not None:
                await self._show_page(message, cid, target)

    async def _show_page(self, message, cid: int, pos: int) -> None:
        row, photo, caption, markup = await self._page(cid, pos)
        try:
            edited = await message.edit_media(
                InputMediaPhoto(photo, caption=caption, parse_mode=ParseMode.HTML), reply_markup=markup)
            self._remember_photo(row["id"], edited)
        except TelegramError as e:
            text = str(e).lower()
            if "not modified" in text or "canceled by new edit" in text:
                return  # already showing this page / a newer change took over
            log.warning(t("log.not_editable", error=e))
            try:  # e.g. Telegram could not fetch the poster: show the page with a placeholder
                await message.edit_media(
                    InputMediaPhoto(placeholder_png(), caption=caption, parse_mode=ParseMode.HTML), reply_markup=markup)
            except TelegramError as e2:
                log.warning(t("log.not_editable", error=e2))
                return
            # the stand-in is not remembered as the poster: the next page change tries the real one again
        self.db.set_carousel(cid, position=pos % len(self.db.carousel_pages(cid)))

    async def _decided(self, q, sid: int, note: str) -> None:
        """Show a decision: redraw the card's current page, or append a note on older single cards."""
        s = self.db.suggestion(sid)
        car = self.db.carousel(s["carousel_id"]) if s and s["carousel_id"] else None
        if car is None:
            await self._mark(q, note)
            return
        cid = car["id"]
        async with self._card_lock(cid):  # not in the middle of a page change
            # The page shown *now* – someone may have paged on since the button was pressed. Then there is
            # nothing to redraw: the note appears when they page back (the page is drawn from the database).
            pos = self.db.carousel(cid)["position"]
            rows = self.db.carousel_pages(cid)
            if rows[pos % len(rows)]["id"] == sid:
                _, _, caption, markup = await self._page(cid, pos)
                try:
                    await q.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
                except TelegramError as e:
                    log.warning(t("log.not_editable", error=e))
        await self._show_wanted(q.message, cid)  # a tap that arrived meanwhile

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
        sent, _ = await self._send_photo_or_placeholder(coll.poster_url or placeholder_png(), caption, markup,
                                                        coll.name)
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
        who = self._who(q)
        # Mark first: both people may press the button at the same time.
        for s in missing:
            self.db.set_suggestion_status(s["id"], "requested", who)
        await q.answer()  # right away – the requests take a moment; the result goes into the message
        results = await asyncio.gather(*(self.seerr.request("movie", s["tmdb_id"]) for s in missing),
                                       return_exceptions=True)
        requested, failed = [], 0
        for s, res in zip(missing, results):
            if isinstance(res, httpx.HTTPStatusError) and res.response.status_code == 409:
                requested.append(s)  # already requested in Jellyseerr itself – on its way all the same
            elif isinstance(res, BaseException):
                self.db.set_suggestion_status(s["id"], s["status"])
                failed += 1
                log_request_failed(s["title"], res)
            else:
                requested.append(s)
                log.info(t("log.requested", title=s["title"], who=who))
        caption, markup = self._collection_view(col_id, card)
        try:
            await q.message.edit_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError as e:
            log.warning(t("log.not_editable", error=e))
        if failed:
            await self.post(t("bot.coll_failed", n=failed), reply_to_message_id=q.message.message_id)
        if not requested:
            return
        try:
            text = await self.llm.say(t("sit.collection_requested"), self.speaker(), {
                "series": card["name"], "titles": [s["title"] for s in requested], "requested_by": who})
        except LLMFailed:
            return
        await self.post(text, reply_to_message_id=q.message.message_id)
