"""Web server: Jellyfin webhook, health endpoint and the admin GUI with its JSON API."""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from aiohttp import web

from . import arr, config, llm, monitor, updates
from .bot import SETTING_LIMITS
from .db import DEFAULT_SETTINGS
from .i18n import gui_texts, language, t
from .monitor import events, health

if TYPE_CHECKING:
    from .main import Runtime

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "web"
COOKIE = "corsarr_session"
SESSION_TTL = 7 * 24 * 3600
RUNTIME = web.AppKey("runtime", object)
SESSIONS = web.AppKey("sessions", dict)
TASKS = web.AppKey("tasks", set)


# --- helpers ---------------------------------------------------------------------

def _rt(request: web.Request) -> "Runtime":
    return request.app[RUNTIME]


def _background(request: web.Request, coro) -> None:
    tasks: set = request.app[TASKS]
    task = asyncio.create_task(_safe(coro))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _safe(coro) -> None:
    try:
        await coro
    except Exception:
        log.exception(t("log.webhook_failed"))


def _remote(request: web.Request) -> str:
    return request.remote or "?"


def _session_valid(request: web.Request) -> bool:
    token = request.cookies.get(COOKIE, "")
    expiry = request.app[SESSIONS].get(token)
    if not expiry or expiry < time.time():
        request.app[SESSIONS].pop(token, None)
        return False
    request.app[SESSIONS][token] = time.time() + SESSION_TTL  # sliding expiry
    return True


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    if path.startswith("/api/") and path not in ("/api/login", "/api/i18n"):
        # Login only when ADMIN_PASSWORD is set; by default the GUI is open (meant for the home network).
        if _rt(request).cfg.admin_password and not _session_valid(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        # Custom header: a cross-site form or image cannot set it, so this blocks CSRF.
        if request.method != "GET" and request.headers.get("X-Corsarr") != "1":
            return web.json_response({"error": "missing header"}, status=403)
    return await handler(request)


# --- webhook -----------------------------------------------------------------------

async def _webhook_event(request: web.Request, service: str) -> dict | web.Response:
    """Check the shared secret (header or ?secret=) and parse the JSON body, or return the error response."""
    # X-Filmbot-Secret: header name from before the rename – Jellyfin setups that still send it keep working.
    given = (request.headers.get("X-Corsarr-Secret") or request.headers.get("X-Filmbot-Secret")
             or request.query.get("secret", ""))
    secret = _rt(request).cfg.webhook_secret
    if not secret or not hmac.compare_digest(given.encode(), secret.encode()):
        log.warning(t("log.webhook_forbidden", remote=_remote(request)))
        health.error(service, t("log.webhook_forbidden", remote=_remote(request)))
        return web.Response(status=403)
    try:
        event = json.loads(await request.text())
    except json.JSONDecodeError:
        return web.Response(status=400, text="invalid json")
    return event if isinstance(event, dict) else web.Response(status=400, text="invalid json")


async def jellyfin_webhook(request: web.Request) -> web.Response:
    rt = _rt(request)
    event = await _webhook_event(request, "webhook")
    if isinstance(event, web.Response):
        return event
    kind = event.get("event") or event.get("NotificationType")
    log.info(t("log.webhook", kind=kind, item=event.get("itemId") or event.get("ItemId")))
    health.ok("webhook", t("check.webhook_last", kind=kind))
    if kind == "PlaybackStop":
        if rt.feedback is None:
            log.warning(t("log.webhook_not_ready"))
            return web.Response(status=503, text="bot not running")
        _background(request, rt.feedback.on_playback_stop(event))
    return web.Response(text="ok")


async def sonarr_webhook(request: web.Request) -> web.Response:
    return await _arr_webhook(request, "sonarr", arr.record_sonarr)


async def radarr_webhook(request: web.Request) -> web.Response:
    return await _arr_webhook(request, "radarr", arr.record_radarr)


async def _arr_webhook(request: web.Request, service: str, record) -> web.Response:
    """Imports are only stored here; the bot's job bundles and sends them, even after a restart."""
    event = await _webhook_event(request, service)
    if isinstance(event, web.Response):
        return event
    kind = event.get("eventType", "?")
    stored = record(_rt(request).db, event)
    log.info(t("log.arr_event", service=service.capitalize(), kind=kind, n=stored))
    health.ok(service, t("check.webhook_last", kind=kind))
    return web.Response(text="ok")


async def health_endpoint(_: web.Request) -> web.Response:
    return web.Response(text="ok")


# --- GUI: pages and auth -----------------------------------------------------------

async def index(_: web.Request) -> web.Response:
    # The version in the asset URLs makes browsers fetch the new app.js/style.css after an update
    # instead of reusing a cached copy.
    version = (updates.current_version() or str(int((STATIC / "app.js").stat().st_mtime)))[:12]
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace('/static/app.js"', f'/static/app.js?v={version}"')
    html = html.replace('/static/style.css"', f'/static/style.css?v={version}"')
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def api_i18n(_: web.Request) -> web.Response:
    return web.json_response({"lang": language(), "texts": gui_texts()})


async def api_login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    given = str(body.get("password", "")).encode()
    expected = _rt(request).cfg.admin_password.encode()
    if not expected or not hmac.compare_digest(given, expected):
        log.warning(t("log.gui_login_failed", remote=_remote(request)))
        await asyncio.sleep(1)  # slows down guessing
        return web.json_response({"error": t("gui.login_failed")}, status=403)
    token = secrets.token_urlsafe(32)
    request.app[SESSIONS][token] = time.time() + SESSION_TTL
    log.info(t("log.gui_login", remote=_remote(request)))
    resp = web.json_response({"ok": True})
    resp.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="Strict", path="/")
    return resp


async def api_logout(request: web.Request) -> web.Response:
    request.app[SESSIONS].pop(request.cookies.get(COOKIE, ""), None)
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE, path="/")
    return resp


# --- GUI: status and events -----------------------------------------------------

def status_payload(rt: "Runtime") -> dict:
    return {
        "state": rt.state,
        "state_detail": rt.state_detail,
        "outage": bool(rt.corsarr and rt.corsarr.down),
        "missing": list(rt.cfg.errors),
        "services": health.snapshot(),
        "bot_username": rt.corsarr.username if rt.corsarr and rt.state == "running" else "",
        "now": time.time(),
        "started_at": rt.started_at,
        "data_dir": str(rt.cfg.data_dir),
        "log_file": str(rt.cfg.log_dir / "corsarr.log"),
        "language": language(),
        "auth": bool(rt.cfg.admin_password),
        "version": updates.current_version() or "",
    }


async def api_status(request: web.Request) -> web.Response:
    return web.json_response(status_payload(_rt(request)))


async def api_check(request: web.Request) -> web.Response:
    rt = _rt(request)
    await rt.check()
    return web.json_response(status_payload(rt))


async def api_restart(request: web.Request) -> web.Response:
    rt = _rt(request)
    await rt.restart()
    return web.json_response(status_payload(rt))


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


async def api_claude_models(request: web.Request) -> web.Response:
    """Models for the picker. Uses a key typed into the form (not saved yet), else the saved one."""
    rt = _rt(request)
    key = str((await _json_body(request)).get("api_key") or rt.cfg.anthropic_api_key)
    if not key:
        return web.json_response({"models": [], "error": t("gui.options_need_key")})
    try:
        models = await llm.available_models(key)
    except Exception as e:  # wrong key, no network – the form falls back to a text field
        return web.json_response({"models": [], "error": monitor.describe_error(e)})
    return web.json_response({"models": models, "recommended": llm.RECOMMENDED_MODEL, "error": ""})


async def api_jellyfin_users(request: web.Request) -> web.Response:
    """Jellyfin accounts for the picker. Uses address/key typed into the form, else the saved ones."""
    rt = _rt(request)
    body = await _json_body(request)
    url = str(body.get("url") or rt.cfg.jellyfin_url).rstrip("/")
    key = str(body.get("api_key") or rt.cfg.jellyfin_api_key)
    if not url or not key:
        return web.json_response({"users": [], "error": t("gui.options_need_jellyfin")})
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{url}/Users",
                                 headers={"Authorization": f'MediaBrowser Client="Corsarr", Token="{key}"'})
            r.raise_for_status()
            users = sorted(u["Name"] for u in r.json() if u.get("Name"))
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return web.json_response({"users": [], "error": monitor.describe_error(e)})
    return web.json_response({"users": users, "error": ""})


async def api_update(request: web.Request) -> web.Response:
    """Installed vs. latest version; ?force=1 skips the cache (button "Check for updates")."""
    rt = _rt(request)
    info = await updates.check(force=request.query.get("force") == "1")
    return web.json_response({**info, "kind": updates.install_kind(), "updating": updates.updating(rt.cfg.data_dir),
                              "log": updates.log_tail(rt.cfg.data_dir)})


async def api_update_start(request: web.Request) -> web.Response:
    if updates.install_kind() != "service":
        return web.json_response({"error": t("gui.update_not_possible")}, status=400)
    updates.request_update()
    log.info(t("log.update_requested", remote=_remote(request)))
    return web.json_response({"ok": True})


async def api_events(request: web.Request) -> web.Response:
    try:
        after = int(request.query.get("after", "0"))
    except ValueError:
        after = 0
    return web.json_response({"events": events.since(after), "now": time.time()})


# --- GUI: configuration ---------------------------------------------------------

def config_payload(rt: "Runtime") -> dict:
    cfg = rt.cfg
    fields = []
    for f in config.FIELDS:
        value = cfg.get(f.name)
        fields.append({
            "name": f.name, "group": f.group, "required": f.required, "secret": f.secret,
            "kind": f.kind, "choices": list(f.choices), "editable": f.editable,
            "app_restart": f.app_restart, "default": f.default,
            "value": "" if f.secret else value, "is_set": bool(value),
            "source": cfg.sources.get(f.name, "default"), "error": cfg.errors.get(f.name, ""),
        })
    settings = rt.db.settings()
    behaviour = []
    for key, default in DEFAULT_SETTINGS.items():
        lo, hi = SETTING_LIMITS.get(key, (None, None))
        behaviour.append({"key": key, "value": settings[key], "kind": type(default).__name__,
                          "min": lo, "max": hi})
    return {"fields": fields, "settings": behaviour}


async def api_config(request: web.Request) -> web.Response:
    return web.json_response(config_payload(_rt(request)))


async def api_config_save(request: web.Request) -> web.Response:
    rt = _rt(request)
    body = await request.json()
    values: dict = body.get("values") or {}
    reset = set(body.get("reset") or [])
    overrides = config.read_overrides(rt.cfg.data_dir)
    errors: dict[str, str] = {}
    changed: list[str] = []
    assigned: set[str] = set()

    for name, raw in values.items():
        f = config.FIELD_BY_NAME.get(name)
        if f is None or not f.editable:
            continue
        value = str(raw).strip()
        if not value:
            if f.secret:
                continue  # empty secret field = keep the current value
            reset.add(name)  # emptied field = back to the environment value
            continue
        if err := config.validate(f, value):
            errors[name] = err
            continue
        assigned.add(name)
        if overrides.get(name) != value:
            overrides[name] = value
            changed.append(name)
    for name in reset - assigned:
        if name in overrides:
            del overrides[name]
            changed.append(name)
    if errors:
        return web.json_response({"errors": errors}, status=400)
    if not changed:
        return web.json_response({"restart": False, "app_restart": False, **config_payload(rt)})

    config.write_overrides(rt.cfg.data_dir, overrides)
    log.info(t("log.config_saved", fields=", ".join(sorted(set(changed)))))
    if "ADMIN_PASSWORD" in changed:  # sign out every other session
        current = request.cookies.get(COOKIE, "")
        sessions = request.app[SESSIONS]
        for token in list(sessions):
            if token != current:
                sessions.pop(token)
    app_restart = any(config.FIELD_BY_NAME[n].app_restart for n in changed)
    needs_restart = any(not config.FIELD_BY_NAME[n].app_restart and n != "ADMIN_PASSWORD" for n in changed)
    if needs_restart:
        await rt.restart()
    else:
        rt.cfg = config.load()
    return web.json_response({"restart": needs_restart, "app_restart": app_restart, **config_payload(rt)})


async def api_settings_save(request: web.Request) -> web.Response:
    rt = _rt(request)
    body: dict = await request.json()
    applied = {}
    for key, value in body.items():
        if key not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[key]
        if isinstance(default, bool):
            value = bool(value)
        else:
            try:
                value = int(value)
            except (TypeError, ValueError):
                return web.json_response({"errors": {key: t("cfg.not_int", name=key, value=value)}},
                                         status=400)
            lo, hi = SETTING_LIMITS.get(key, (value, value))
            value = max(lo, min(hi, value))
        rt.db.set_setting(key, value)
        applied[key] = value
    if applied:
        log.info(t("log.settings_changed", changes=applied))
    return web.json_response(config_payload(rt))


# --- app ---------------------------------------------------------------------------

def build_app(runtime: "Runtime") -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app[RUNTIME] = runtime
    app[SESSIONS] = {}
    app[TASKS] = set()
    app.router.add_post("/jellyfin", jellyfin_webhook)
    app.router.add_post("/sonarr", sonarr_webhook)
    app.router.add_post("/radarr", radarr_webhook)
    app.router.add_get("/health", health_endpoint)
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC)
    app.router.add_get("/api/i18n", api_i18n)
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/logout", api_logout)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/check", api_check)
    app.router.add_post("/api/restart", api_restart)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/update", api_update)
    app.router.add_post("/api/options/claude-models", api_claude_models)
    app.router.add_post("/api/options/jellyfin-users", api_jellyfin_users)
    app.router.add_post("/api/update", api_update_start)
    app.router.add_get("/api/config", api_config)
    app.router.add_put("/api/config", api_config_save)
    app.router.add_put("/api/settings", api_settings_save)
    return app


def local_url(host: str, port: int) -> str:
    """Address to open in a browser on this machine."""
    shown = "localhost" if host in ("0.0.0.0", "::", "") else host
    return f"http://{shown}:{port}/"


async def start(runtime: "Runtime", host: str, port: int) -> web.AppRunner:
    runner = web.AppRunner(build_app(runtime))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info(t("log.web_listening", url=local_url(host, port)))
    return runner
