"""The bot's periodic jobs (registered in main.py): outage probe, download notifications, budget warnings,
catching up missed imports, feedback questions and data retention. Mixed into CorsarrBot (bot.py)."""
from __future__ import annotations

import functools
import logging

import httpx
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from . import arr, retention, updates, usage
from .i18n import t, use_language
from .llm import LLMFailed, LLMUnavailable

log = logging.getLogger(__name__)

# Job intervals in seconds (first run after `first` seconds), see main.py.
FEEDBACK_INTERVAL, FEEDBACK_FIRST = 600, 30
OUTAGE_INTERVAL, OUTAGE_FIRST = 300, 300
DOWNLOADS_INTERVAL, DOWNLOADS_FIRST = 60, 10
CATCH_UP_INTERVAL, CATCH_UP_FIRST = 600, 20
BUDGET_INTERVAL, BUDGET_FIRST = 600, 60
RETENTION_INTERVAL, RETENTION_FIRST = 24 * 3600, 120
UPDATE_NEWS_INTERVAL, UPDATE_NEWS_FIRST = 3600, 90  # after an update; retried hourly while GitHub/the model fail

ANNOUNCED = "announced_version"  # state key: the version whose news the group got (or that had none)


def in_chat_language(method):
    """Run a handler in the group's language – for everything the bot does without a fresh message."""
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        with use_language(self.chat_language()):
            return await method(self, *args, **kwargs)
    return wrapper


class JobsMixin:
    async def _probe(self) -> bool:
        if self._probing:
            return False  # a probe is under way (job and a message at once) – it posts "back" once
        self._probing = True
        try:
            await self.llm.ping()
        except (LLMUnavailable, LLMFailed):
            return False
        finally:
            self._probing = False
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
            text, level = t("bot.budget_reached", cost=usage.usd(cost), budget=usage.usd(budget)), "100"
        elif cost >= usage.WARN_SHARE * budget and not warned:
            text, level = t("bot.budget_warning", cost=usage.usd(cost), budget=usage.usd(budget),
                            pct=round(100 * cost / budget)), "80"
        else:
            return
        target = int(self.cfg.get("ADMIN_CHAT_ID") or 0) or self.cfg.chat_id
        try:
            await self.bot.send_message(target, text)
        except TelegramError as e:
            log.warning(t("log.notify_failed", error=e))
            return
        self.db.set_state(f"budget_warned:{month}", level)
        log.info(t("log.budget_warned", level=level, cost=f"{cost:.4f}", budget=f"{budget:.2f}"))

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

    @in_chat_language
    async def job_update_news(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """After an update: tell the group once what changed for them. The release notes are written for
        admins – the model keeps only what people notice in the chat and posts nothing if that's none."""
        current = updates.current_version()
        new = updates.version_key(current)
        if new is None:
            return  # a dev build: no release notes to tell from
        seen = self.db.get_state(ANNOUNCED)
        if seen == current:
            return
        have = updates.version_key(seen)
        if have is None or new < have:
            self.db.set_state(ANNOUNCED, current)  # first start or a downgrade: nothing to announce
            return
        if self.down:
            return
        try:
            changes = await updates.changes_since(seen, current)
            if changes:
                notes = "\n\n".join(f"{c['tag']}\n{c['notes']}" for c in changes)
                news = await self.llm.update_news(notes, self.speaker())
                if news.message:
                    await self.post(news.message)
                    log.info(t("log.update_news", version=current))
                else:
                    log.info(t("log.update_news_none", version=current))
        except (httpx.HTTPError, LLMUnavailable, LLMFailed, TelegramError) as e:
            log.warning(t("log.update_news_failed", version=current, error=e))
            return  # again in an hour
        self.db.set_state(ANNOUNCED, current)

    async def job_retention(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Once a day: drop what nothing reads any more (see retention.py)."""
        try:
            removed = retention.run(self.db, self.cfg.data_dir)
        except Exception:
            log.exception(t("log.retention_failed"))
            return
        if any(removed.values()):
            log.info(t("log.retention", summary=", ".join(f"{k} {v}" for k, v in removed.items() if v)))
