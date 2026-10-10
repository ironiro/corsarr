"""Entry point: python -m corsarr

The web server (GUI and Jellyfin webhook) always runs. The Telegram bot starts once the
configuration is complete and can be restarted in-process when it changes in the GUI.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import signal
import time
from logging.handlers import RotatingFileHandler

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from . import backup, config, usage, web
from .bot import CorsarrBot
from .checks import run_checks
from .db import DB, DatabaseTooNew
from .feedback import FeedbackService
from .i18n import t
from .jellyfin import Jellyfin
from .jellyseerr import Jellyseerr
from . import llm as llm_module
from .llm import LLM
from .monitor import events, health
from .profile import ProfileBuilder
from .recommender import Recommender

log = logging.getLogger("corsarr")

HEALTH_INTERVAL = 300  # seconds between automatic connection checks


def setup_logging(cfg: config.Config) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(cfg.log_level)
    file_handler = RotatingFileHandler(cfg.log_dir / "corsarr.log", maxBytes=2_000_000, backupCount=5,
                                       encoding="utf-8")
    console = logging.StreamHandler()
    for h in (file_handler, console):
        h.setFormatter(fmt)
        root.addHandler(h)
    # Updates restart the program often: show the entries from before the restart again.
    events.restore(cfg.log_dir / "corsarr.log", start_messages=(t("log.process_start"),),
                   fallback_start=(t("log.web_listening", url=""),))
    root.addHandler(events)
    # Per-request lines of the HTTP libraries (and the GUI's own polling) would flood the event log.
    for noisy in ("httpx", "httpx2", "httpcore", "aiohttp.access", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    log.info(t("log.process_start"))  # where the event log draws the restart divider next time


def ensure_webhook_secret(cfg: config.Config) -> config.Config:
    """Nobody has to choose the webhook secret by hand – it only has to match Jellyfin."""
    if cfg.webhook_secret:
        return cfg
    overrides = config.read_overrides(cfg.data_dir)
    overrides["WEBHOOK_SECRET"] = secrets.token_urlsafe(24)
    config.write_overrides(cfg.data_dir, overrides)
    return config.load()


def print_setup_hint(cfg: config.Config) -> None:
    """Console block while the configuration is incomplete: where to set it up."""
    lines = [t("cli.setup_title"), t("cli.setup_url", url=web.local_url(cfg.webhook_host, cfg.webhook_port))]
    if cfg.webhook_host in ("0.0.0.0", "::", ""):
        lines.append(t("cli.setup_lan", port=cfg.webhook_port))
    lines.append(t("cli.setup_missing"))
    print("\n" + "\n".join(f"  {line}" for line in lines) + "\n", flush=True)


class Runtime:
    """Owns the configuration, the database and the (re)startable Telegram bot."""

    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.db: DB | None = None
        self.db_error = ""
        self._open_db()
        self.started_at = time.time()
        self.state = "stopped"  # 'starting' | 'running' | 'unconfigured' | 'error' | 'stopped'
        self.state_detail = ""
        self.app: Application | None = None
        self.corsarr: CorsarrBot | None = None
        self.feedback: FeedbackService | None = None
        self.jellyfin: Jellyfin | None = None
        self.seerr: Jellyseerr | None = None
        self.llm: LLM | None = None
        self._lock = asyncio.Lock()

    def _open_db(self) -> None:
        self.db, self.db_error = None, ""
        try:
            self.db = DB(self.cfg.db_path)
        except DatabaseTooNew as e:
            # Keep the web interface up so the problem is visible there; the bot itself can't start.
            self.db_error = t("log.db_too_new", found=e.found, path=self.cfg.db_path)
            log.error(self.db_error)

    # --- lifecycle -------------------------------------------------------------
    async def start(self) -> None:
        async with self._lock:
            await self._start()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def restore(self, settings: dict, database: bytes | None) -> None:
        """Replace database and settings with a backup's and start again (see backup.py)."""
        async with self._lock:
            await self._stop()
            if self.db is not None:
                self.db.conn.close()
            keep = backup.restore(self.cfg.data_dir, settings, database)
            log.info(t("log.backup_restored", keep=keep))
            self.cfg = config.load()
            logging.getLogger().setLevel(self.cfg.log_level)
            self._open_db()
            await self._start()
        await self.check()

    async def restart(self) -> None:
        async with self._lock:
            await self._stop()
            self.cfg = config.load()
            logging.getLogger().setLevel(self.cfg.log_level)
            await self._start()
        await self.check()

    async def _start(self) -> None:
        cfg = self.cfg
        if self.db is None:
            self.state, self.state_detail = "error", self.db_error
            return
        if not cfg.complete:
            self.state, self.state_detail = "unconfigured", ", ".join(cfg.errors)
            log.warning(t("log.config_incomplete", fields=", ".join(cfg.errors)))
            return
        self.state, self.state_detail = "starting", ""
        log.info(t("log.bot_starting"))
        self.jellyfin = Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key, cfg.jellyfin_user)
        self.seerr = Jellyseerr(cfg.jellyseerr_url, cfg.jellyseerr_api_key)
        self.seerr.set_streaming(cfg.streaming_region, cfg.streaming_ids)
        self.llm = llm_module.create(cfg)
        self.llm.on_usage = lambda kind, tokens: self.db.add_usage(self.llm.provider, self.llm.model, kind, tokens)
        self.llm.budget_reached = lambda: usage.budget_reached(self.db, self.cfg)  # self.cfg: budget changes live
        profiles = ProfileBuilder(self.db, self.jellyfin)
        recommender = Recommender(self.db, self.jellyfin, self.seerr, self.llm, profiles)
        self.feedback = FeedbackService(self.db, self.jellyfin, self.seerr, profiles)
        self.corsarr = CorsarrBot(cfg, self.db, self.jellyfin, self.seerr, self.llm, recommender, self.feedback)
        self.feedback.notifier = self.corsarr.ask_feedback

        # Generous timeouts: sending a poster makes Telegram fetch or upload an image first.
        app = (Application.builder().token(cfg.telegram_token).concurrent_updates(True)
               .connect_timeout(15).read_timeout(30).write_timeout(30).media_write_timeout(60).build())
        chat = filters.Chat(chat_id=cfg.chat_id)
        app.add_handler(MessageHandler(chat & filters.TEXT, self.corsarr.on_message))
        # Private messages only serve to learn the admin's chat id (for budget warnings)
        app.add_handler(MessageHandler(filters.ChatType.PRIVATE, self.corsarr.on_private))
        app.add_handler(CallbackQueryHandler(self.corsarr.on_callback))
        app.add_error_handler(self._on_error)
        self.app = app
        try:
            await app.initialize()
            self.corsarr.app = app
            self.corsarr.bot_id, self.corsarr.username = app.bot.id, app.bot.username
            await self.corsarr.load_genres()
            app.job_queue.run_repeating(self.corsarr.job_feedback, interval=600, first=30)
            app.job_queue.run_repeating(self.corsarr.job_outage, interval=300, first=300)
            app.job_queue.run_repeating(self.corsarr.job_downloads, interval=60, first=10)
            app.job_queue.run_repeating(self.corsarr.job_catch_up, interval=600, first=20)
            app.job_queue.run_repeating(self.corsarr.job_budget, interval=600, first=60)
            await app.start()
            await app.updater.start_polling(
                allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY], drop_pending_updates=True,
                error_callback=lambda e: health.error("telegram", f"{type(e).__name__}: {e}"))
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            log.error(t("log.bot_start_failed", error=detail))
            health.error("telegram", detail)  # everything in this block talks to Telegram
            await self._stop()
            self.state, self.state_detail = "error", detail
            return
        health.ok("telegram")
        self.state = "running"
        log.info(t("log.signed_in", username=app.bot.username, chat=cfg.chat_id, model=cfg.model,
                   lang=cfg.language))

    async def _stop(self) -> None:
        app, was_running, model = self.app, self.state == "running", self.llm
        self.app = self.corsarr = self.feedback = self.llm = None
        if model is not None:
            await model.close()
        if app is not None:
            try:
                if app.updater and app.updater.running:
                    await app.updater.stop()
                if app.running:
                    await app.stop()
                await app.shutdown()
            except Exception:
                log.exception(t("log.bot_stopped"))
        for client in (self.jellyfin, self.seerr):
            if client is not None:
                await client.close()
        self.jellyfin = self.seerr = None
        self.state, self.state_detail = "stopped", ""
        if was_running:
            log.info(t("log.bot_stopped"))

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error(t("log.telegram_error", error=context.error), exc_info=context.error)

    # --- status ------------------------------------------------------------------
    async def check(self, manual: bool = False) -> None:
        """`manual`: the "Check now" button – also lets Sonarr/Radarr send a test event."""
        running = self.state == "running"
        await run_checks(self.cfg, bot=self.app.bot if running and self.app else None,
                         jellyfin=self.jellyfin if running else None,
                         seerr=self.seerr if running else None,
                         llm=self.llm if running else None, send_tests=manual)

    async def health_loop(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("health check")
            await asyncio.sleep(HEALTH_INTERVAL)


async def amain() -> None:
    cfg = config.load()
    setup_logging(cfg)
    cfg = ensure_webhook_secret(cfg)
    runtime = Runtime(cfg)
    web_runner = await web.start(runtime, cfg.webhook_host, cfg.webhook_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # Windows: Ctrl+C arrives as KeyboardInterrupt
            pass

    await runtime.start()
    if not cfg.complete:
        print_setup_hint(cfg)
        log.warning(t("log.setup_needed", url=web.local_url(cfg.webhook_host, cfg.webhook_port)))
    health_task = asyncio.create_task(runtime.health_loop())
    try:
        await stop.wait()
    finally:
        health_task.cancel()
        await runtime.stop()
        await web_runner.cleanup()


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
