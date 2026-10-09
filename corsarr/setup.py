"""Setup assistant: tests with values that are not saved yet, and setting up the other services' webhooks.

Nothing here changes Corsarr's own configuration – the assistant saves through the normal config API once
a step's test passed.
"""
from __future__ import annotations

from typing import Any

import httpx

from . import llm
from .jellyfin import Jellyfin
from .jellyseerr import Jellyseerr
from .monitor import describe_error

TELEGRAM_API = "https://api.telegram.org"
HOOK_NAME = "Corsarr"
JELLYFIN_TEMPLATE = """{
  "event": "{{NotificationType}}",
  "itemId": "{{ItemId}}",
  "itemType": "{{ItemType}}",
  "playedToCompletion": "{{PlayedToCompletion}}",
  "positionTicks": "{{PlaybackPositionTicks}}"
}"""


class SetupError(Exception):
    """A step failed; the message is meant for the user (i18n key + values)."""

    def __init__(self, key: str, **values):
        super().__init__(key)
        self.key, self.values = key, values


# --- Telegram -------------------------------------------------------------------------------

async def telegram(token: str, client: httpx.AsyncClient | None = None) -> dict:
    """Who the bot is, whether it can read group messages, and the groups it has seen messages from.

    The chat id no longer has to be looked up: add the bot to the group, write something there, and the
    group shows up here. Only works while no other program polls this bot (Corsarr itself included).
    """
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        me = await _telegram_call(client, token, "getMe")
        updates = await _telegram_call(client, token, "getUpdates",
                                       {"timeout": 0, "allowed_updates": ["message", "my_chat_member"]})
    finally:
        if own:
            await client.aclose()
    chats: dict[int, dict] = {}
    for update in reversed(updates):  # newest first
        for key in ("message", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat") or {}
            if chat.get("type") in ("group", "supergroup") and chat["id"] not in chats:
                chats[chat["id"]] = {"id": chat["id"], "title": chat.get("title") or str(chat["id"])}
    return {"username": me.get("username", ""), "privacy": not me.get("can_read_all_group_messages", False),
            "chats": list(chats.values())}


async def _telegram_call(client: httpx.AsyncClient, token: str, method: str, params: dict | None = None) -> Any:
    try:
        r = await client.post(f"{TELEGRAM_API}/bot{token}/{method}", json=params or {})
    except httpx.HTTPError as e:
        raise SetupError("setup.unreachable", error=describe_error(e)) from None
    if r.status_code in (401, 404):
        raise SetupError("setup.telegram_token")
    if r.status_code == 409:  # another getUpdates/webhook consumer – e.g. the running bot
        raise SetupError("setup.telegram_busy")
    try:
        data = r.json()
    except ValueError:
        raise SetupError("setup.unreachable", error=f"HTTP {r.status_code}") from None
    if not data.get("ok"):
        raise SetupError("setup.unreachable", error=data.get("description") or f"HTTP {r.status_code}")
    return data["result"]


# --- connection tests -------------------------------------------------------------------------

async def test_jellyfin(url: str, api_key: str, user: str) -> str:
    jf = Jellyfin(url.rstrip("/"), api_key, user)
    try:
        uid = await jf.uid()
        genres = await jf.genres()
    except Exception as e:  # wrong address/key/user – shown to the user as is
        raise SetupError("setup.failed", error=describe_error(e)) from None
    finally:
        await jf.close()
    return f"{user} ({uid[:8]}…), {len(genres)} genres"


async def test_seerr(url: str, api_key: str) -> str:
    seerr = Jellyseerr(url.rstrip("/"), api_key)
    try:
        genres = await seerr.genres("movie")
    except Exception as e:
        raise SetupError("setup.failed", error=describe_error(e)) from None
    finally:
        await seerr.close()
    return f"{len(genres)} TMDB genres"


async def test_llm(provider: str, model: str, api_key: str = "", url: str = "") -> str:
    try:
        models = await llm.available_models(provider, api_key, url)
    except Exception as e:
        raise SetupError("setup.failed", error=describe_error(e)) from None
    ids = [m["id"] for m in models]
    if model not in ids and f"{model}:latest" not in ids:
        raise SetupError("setup.model_missing", model=model)
    return model


# --- webhooks -----------------------------------------------------------------------------------

def webhook_urls(base: str, secret: str) -> dict:
    """Addresses to enter in Jellyfin, Sonarr and Radarr; `base` is how the browser reached Corsarr."""
    base = base.rstrip("/")
    return {"jellyfin": f"{base}/jellyfin", "sonarr": f"{base}/sonarr?secret={secret}",
            "radarr": f"{base}/radarr?secret={secret}", "header": "X-Corsarr-Secret", "secret": secret,
            "template": JELLYFIN_TEMPLATE}


async def connect_arr(kind: str, url: str, api_key: str, hook_url: str,
                      client: httpx.AsyncClient | None = None) -> dict:
    """Create (or update) the Corsarr webhook in Sonarr/Radarr: On File Import only, POST to `hook_url`.

    Also reports Telegram connections set up there, which would send every download twice.
    """
    own = client is None
    client = client or httpx.AsyncClient(timeout=20)
    api = f"{url.rstrip('/')}/api/v3"
    headers = {"X-Api-Key": api_key}
    try:
        try:
            r = await client.get(f"{api}/notification", headers=headers)
        except httpx.HTTPError as e:
            raise SetupError("setup.unreachable", error=describe_error(e)) from None
        if r.status_code == 401:
            raise SetupError("setup.arr_key")
        if r.status_code != 200:
            raise SetupError("setup.unreachable", error=f"HTTP {r.status_code}")
        existing = r.json()
        telegram = [n.get("name", "") for n in existing if n.get("implementation") == "Telegram"]
        ours = next((n for n in existing if n.get("implementation") == "Webhook"
                     and f"/{kind}" in str(_field(n, "url"))), None)
        if ours is None:
            schema = (await client.get(f"{api}/notification/schema", headers=headers)).json()
            ours = next(n for n in schema if n.get("implementation") == "Webhook")
        hook = _configure(ours, hook_url)
        if "id" in hook:
            r = await client.put(f"{api}/notification/{hook['id']}", headers=headers, json=hook)
        else:
            r = await client.post(f"{api}/notification", headers=headers, json=hook)
        if r.status_code >= 400:  # Sonarr/Radarr test the address on saving – e.g. not reachable from there
            raise SetupError("setup.arr_rejected", error=_arr_error(r))
    except (httpx.HTTPError, ValueError, StopIteration, KeyError, TypeError) as e:
        raise SetupError("setup.unreachable", error=describe_error(e)) from None
    finally:
        if own:
            await client.aclose()
    return {"updated": "id" in hook, "telegram": telegram}


def _field(notification: dict, name: str) -> Any:
    return next((f.get("value") for f in notification.get("fields", []) if f.get("name") == name), None)


def _configure(notification: dict, hook_url: str) -> dict:
    hook = dict(notification)
    hook["name"] = HOOK_NAME
    hook["enable"] = True
    for key in list(hook):  # every trigger off …
        if key.startswith("on") and isinstance(hook[key], bool):
            hook[key] = False
    hook["onDownload"] = True  # … except "On File Import"
    fields = []
    for f in hook.get("fields", []):
        f = dict(f)
        if f.get("name") == "url":
            f["value"] = hook_url
        elif f.get("name") == "method":
            f["value"] = 1  # POST
        fields.append(f)
    hook["fields"] = fields
    hook.pop("presets", None)
    return hook


def _arr_error(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return f"HTTP {r.status_code}"
    if isinstance(data, list) and data:
        return "; ".join(str(e.get("errorMessage", e)) for e in data if isinstance(e, dict))[:300]
    if isinstance(data, dict):
        return str(data.get("message") or data)[:300]
    return f"HTTP {r.status_code}"
