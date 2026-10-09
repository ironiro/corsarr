"""Active connectivity checks, shared by `python -m corsarr.check` and the GUI's status page."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from telegram import Bot

from .config import PROVIDER_FIELDS, Config
from .i18n import t
from .jellyfin import Jellyfin
from .jellyseerr import Jellyseerr
from . import llm as llm_module
from .llm import LLM
from .monitor import describe_error, health

# Config fields a service needs before it can be checked at all.
NEEDS = {
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
    "jellyfin": ("JELLYFIN_URL", "JELLYFIN_API_KEY", "JELLYFIN_USER"),
    "jellyseerr": ("JELLYSEERR_URL", "JELLYSEERR_API_KEY"),
}


def configured(cfg: Config, service: str) -> bool:
    if service == "llm":
        needs = ("LLM_PROVIDER", *PROVIDER_FIELDS.get(cfg.llm_provider, {}).values())
    else:
        needs = NEEDS[service]
    return not any(name in cfg.errors for name in needs)


async def run_checks(cfg: Config, *, bot: Bot | None = None, jellyfin: Jellyfin | None = None,
                     seerr: Jellyseerr | None = None, llm: LLM | None = None,
                     ping: bool = False) -> dict[str, tuple[bool, str]]:
    """Check every configured service, update the shared health state and return {service: (ok, detail)}.

    Clients of a running bot can be passed in; missing ones are created for the check and closed again.
    `ping=True` makes the language model generate a token (detects a reached spending limit, costs a fraction of a cent);
    otherwise only key and model are verified, which is free.
    """
    own: list[Callable[[], Awaitable[None]]] = []
    if jellyfin is None and configured(cfg, "jellyfin"):
        jellyfin = Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key, cfg.jellyfin_user)
        own.append(jellyfin.close)
    if seerr is None and configured(cfg, "jellyseerr"):
        seerr = Jellyseerr(cfg.jellyseerr_url, cfg.jellyseerr_api_key)
        own.append(seerr.close)
    if llm is None and configured(cfg, "llm"):
        llm = llm_module.create(cfg)
        own.append(llm.close)

    async def jellyfin_info() -> str:
        await jellyfin.uid()  # fails when the shared account does not exist
        genres = await jellyfin.genres()
        return t("check.jellyfin", user=cfg.jellyfin_user, n=len(genres))

    async def seerr_info() -> str:
        mv = await seerr.genres("movie")
        return t("check.jellyseerr", n=len(mv))

    async def llm_info() -> str:
        if ping:
            await llm.ping()
            return t("check.llm_ping", provider=llm.label, model=cfg.model)
        await llm.check_model()
        return t("check.llm_model", provider=llm.label, model=cfg.model)

    async def telegram_info() -> str:
        async def describe(b: Bot) -> str:
            me = await b.get_me()
            chat = await b.get_chat(cfg.chat_id)
            privacy = t("check.privacy_off") if me.can_read_all_group_messages else t("check.privacy_on")
            return t("check.telegram", username=me.username, chat=chat.title, privacy=privacy)
        if bot is not None:
            return await describe(bot)
        async with Bot(cfg.telegram_token) as temp:
            return await describe(temp)

    steps = {"jellyfin": jellyfin_info, "jellyseerr": seerr_info, "llm": llm_info,
             "telegram": telegram_info}
    results: dict[str, tuple[bool, str]] = {}

    async def step(name: str) -> None:
        if not configured(cfg, name):
            health.disabled(name, t("check.not_configured"))
            results[name] = (False, t("check.not_configured"))
            return
        try:
            detail = await asyncio.wait_for(steps[name](), timeout=30)
        except Exception as e:  # report every service, never abort the whole check
            detail = describe_error(e)
            health.error(name, detail)
            results[name] = (False, detail)
        else:
            health.ok(name, detail)
            results[name] = (True, detail)

    try:
        await asyncio.gather(*(step(name) for name in steps))
    finally:
        for close in own:
            await close()

    for hook, never in (("webhook", "check.webhook_never"), ("sonarr", "check.arr_never"),
                        ("radarr", "check.arr_never")):
        if "WEBHOOK_SECRET" in cfg.errors:
            health.disabled(hook, t("check.not_configured"))
        elif health.services[hook].status == "unknown":
            health.services[hook].detail = t(never)
    return {name: results[name] for name in steps}
