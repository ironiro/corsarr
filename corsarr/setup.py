"""Setup assistant: tests with values that are not saved yet, and setting up the other services' webhooks.

Nothing here changes Corsarr's own configuration – the assistant saves through the normal config API once
a step's test passed.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import llm
from .jellyfin import Jellyfin
from .jellyseerr import Jellyseerr
from .i18n import t
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


# --- status checks of the webhook senders --------------------------------------------------------

async def check_jellyfin_hook(url: str, api_key: str, secret: str,
                              client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """Is the Jellyfin Webhook plugin installed, and does one of its destinations point at Corsarr?

    Uses the Jellyfin API key Corsarr has anyway. Returns the plugin version; raises SetupError naming
    what is missing; returns (plugin version, the destination's address). Only fields that are present are
    checked – plugin versions differ a little.
    """
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    headers = {"Authorization": f'MediaBrowser Client="Corsarr", Token="{api_key}"'}
    base = url.rstrip("/")
    try:
        plugins = (await client.get(f"{base}/Plugins", headers=headers)).raise_for_status().json()
        plugin = next((p for p in plugins if str(p.get("Name", "")).lower() == "webhook"), None)
        if plugin is None:
            raise SetupError("check.jf_plugin_missing")
        conf = (await client.get(f"{base}/Plugins/{plugin['Id']}/Configuration", headers=headers)) \
            .raise_for_status().json()
    except httpx.HTTPError as e:
        raise SetupError("setup.unreachable", error=describe_error(e)) from None
    finally:
        if own:
            await client.aclose()
    destinations = [d for key in ("GenericOptions", "GenericFormOptions") for d in conf.get(key) or []]
    ours = [d for d in destinations if urlsplit(str(d.get("WebhookUri", ""))).path.rstrip("/").endswith("/jellyfin")]
    if not ours:
        raise SetupError("check.jf_hook_missing")
    with_secret = [d for d in ours if _jellyfin_secret_ok(d, secret)]
    if not with_secret:
        raise SetupError("check.jf_hook_secret")
    hook = with_secret[0]
    if hook.get("EnableWebhook") is False:
        raise SetupError("check.jf_hook_disabled")
    types = hook.get("NotificationTypes")
    if isinstance(types, list) and all(isinstance(x, str) for x in types) and "PlaybackStop" not in types:
        raise SetupError("check.jf_hook_type")
    return str(plugin.get("Version") or ""), str(hook.get("WebhookUri", ""))


def _jellyfin_secret_ok(destination: dict, secret: str) -> bool:
    headers = {str(h.get("Key", "")).lower(): str(h.get("Value", "")) for h in destination.get("Headers") or []}
    return secret in (headers.get("x-corsarr-secret"), headers.get("x-filmbot-secret")) \
        or f"secret={secret}" in str(destination.get("WebhookUri", ""))


async def check_arr(kind: str, url: str, api_key: str, secret: str, send_test: bool = False,
                    client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """Sonarr/Radarr reachable, Corsarr's webhook there and active – and, with `send_test`, let it send its
    test event to Corsarr, which checks the whole way back. Returns (version, webhook address); raises
    SetupError."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    api = f"{url.rstrip('/')}/api/v3"
    headers = {"X-Api-Key": api_key}
    try:
        r = await client.get(f"{api}/system/status", headers=headers)
        if r.status_code == 401:
            raise SetupError("setup.arr_key")
        version = str(r.raise_for_status().json().get("version", ""))
        existing = (await client.get(f"{api}/notification", headers=headers)).raise_for_status().json()
        hook = next((n for n in existing if n.get("implementation") == "Webhook"
                     and f"/{kind}" in str(_field(n, "url")) and f"secret={secret}" in str(_field(n, "url"))), None)
        if hook is None:
            raise SetupError("check.arr_hook_missing")
        if not hook.get("enable", True) or not hook.get("onDownload", False):
            raise SetupError("check.arr_hook_disabled")
        if send_test:
            r = await client.post(f"{api}/notification/test", headers=headers, json=hook)
            if r.status_code >= 400:
                raise SetupError("check.arr_test_failed", error=_arr_error(r))
    except httpx.HTTPError as e:
        raise SetupError("setup.unreachable", error=describe_error(e)) from None
    except (ValueError, TypeError, AttributeError):
        raise SetupError("setup.unreachable", error="unexpected answer") from None
    finally:
        if own:
            await client.aclose()
    return version, str(_field(hook, "url"))


# --- does a webhook address lead to this Corsarr? -----------------------------------------------------

INSTANCE_FILE = "instance-id"


def instance_id(data_dir: Path) -> str:
    """Random id of this installation; /health?instance=1 answers with it (see points_here)."""
    path = data_dir / INSTANCE_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        value = secrets.token_hex(8)
        path.write_text(value, encoding="utf-8")
        return value


async def points_here(hook_url: str, own_id: str, client: httpx.AsyncClient | None = None) -> str | None:
    """None when the address in Jellyfin/Sonarr/Radarr leads to this Corsarr, else what is wrong.

    Asks the address itself instead of comparing IPs, so Docker port mappings and reverse proxies are fine:
    whatever answers there must report this installation's id. (Corsarr has to reach the address the same
    way the other service does – on a home network it does.)
    """
    parts = urlsplit(hook_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return t("check.hook_bad_url", url=hook_url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path.rsplit('/', 1)[0]}"
    own = client is None
    client = client or httpx.AsyncClient(timeout=8)
    try:
        r = await client.get(f"{base}/health", params={"instance": "1"})
        found = r.json().get("instance") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        return t("check.hook_unreachable", address=parts.netloc)
    finally:
        if own:
            await client.aclose()
    return None if found == own_id else t("check.hook_elsewhere", address=parts.netloc)
