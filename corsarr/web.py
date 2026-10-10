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

from . import arr, backup, config, llm, monitor, setup, updates
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
    health.services["webhook"].event = t("check.webhook_last", kind=kind)
    health.ok("webhook", health.services["webhook"].event)
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
    if _rt(request).db is None:
        return web.Response(status=503, text="database unavailable")
    stored = record(_rt(request).db, event)
    log.info(t("log.arr_event", service=service.capitalize(), kind=kind, n=stored))
    health.services[service].event = t("check.webhook_last", kind=kind)
    health.ok(service, health.services[service].event)
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
        "llm": {"provider": rt.cfg.llm_provider, "model": rt.cfg.model,
                "name": config.PROVIDER_NAMES.get(rt.cfg.llm_provider, rt.cfg.llm_provider),
                "recommended": rt.cfg.llm_provider == config.RECOMMENDED_PROVIDER},
    }


async def api_status(request: web.Request) -> web.Response:
    return web.json_response(status_payload(_rt(request)))


async def api_check(request: web.Request) -> web.Response:
    rt = _rt(request)
    await rt.check(manual=True)
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


async def api_models(request: web.Request) -> web.Response:
    """Models for the picker of the selected provider. Uses a key or address typed into the form
    (not saved yet), else the saved one."""
    rt = _rt(request)
    body = await _json_body(request)
    provider = str(body.get("provider") or rt.cfg.llm_provider)
    if provider not in config.PROVIDERS:
        return web.json_response({"models": [], "error": t("cfg.not_choice", name="LLM_PROVIDER",
                                                              choices=", ".join(config.PROVIDERS))})
    names = config.PROVIDER_FIELDS[provider]
    key = str(body.get("api_key") or (rt.cfg.get(names["key"]) if "key" in names else ""))
    url = str(body.get("url") or (rt.cfg.get(names["url"]) if "url" in names else ""))
    if "key" in names and not key:
        return web.json_response({"models": [], "error": t("gui.options_need_key")})
    try:
        models = await llm.available_models(provider, key, url)
    except Exception as e:  # wrong key, no network, server not running – the form falls back to a text field
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
    info = await updates.check(updates.channel(rt.cfg.get("UPDATE_CHANNEL")), force=request.query.get("force") == "1")
    return web.json_response({**info, "kind": updates.install_kind(), "updating": updates.updating(rt.cfg.data_dir),
                              "log": updates.log_tail(rt.cfg.data_dir)})


async def api_update_start(request: web.Request) -> web.Response:
    if updates.install_kind() != "service":
        return web.json_response({"error": t("gui.update_not_possible")}, status=400)
    # The target is worked out here, never taken from the request.
    info = await updates.check(updates.channel(_rt(request).cfg.get("UPDATE_CHANNEL")), force=True)
    if not info.get("target"):
        return web.json_response({"error": info.get("error") or t("gui.update_none")}, status=400)
    if info.get("downgrade") and not (await _json_body(request)).get("downgrade"):
        return web.json_response({"error": t("gui.update_downgrade_confirm", version=info["target"])}, status=409)
    updates.request_update(info["target"])
    log.info(t("log.update_requested", remote=_remote(request)))
    return web.json_response({"ok": True})


# --- GUI: setup assistant -----------------------------------------------------------

def _setup_error(e: setup.SetupError) -> web.Response:
    return web.json_response({"ok": False, "error": t(e.key, **e.values)}, status=400)


async def api_setup_telegram(request: web.Request) -> web.Response:
    """Check a bot token and list the groups the bot has seen (for picking the chat id)."""
    rt = _rt(request)
    token = str((await _json_body(request)).get("token") or rt.cfg.telegram_token)
    if not token:
        return web.json_response({"ok": False, "error": t("cfg.missing", name="TELEGRAM_BOT_TOKEN")}, status=400)
    try:
        info = await setup.telegram(token)
    except setup.SetupError as e:
        if e.key == "setup.telegram_busy" and rt.state == "running" and token == rt.cfg.telegram_token:
            info = {"username": rt.corsarr.username if rt.corsarr else "", "privacy": None,
                    "chats": [{"id": rt.cfg.chat_id, "title": ""}]}  # the running bot already uses this token
        else:
            return _setup_error(e)
    return web.json_response({"ok": True, **info})


async def api_setup_test(request: web.Request) -> web.Response:
    """Test one service with values from the form (not saved yet); empty values mean the saved ones."""
    rt = _rt(request)
    body = await _json_body(request)
    given = body.get("values") if isinstance(body.get("values"), dict) else {}
    value = lambda name: str(given.get(name) or rt.cfg.get(name)).strip()
    service = body.get("service")
    try:
        if service == "jellyfin":
            detail = await setup.test_jellyfin(value("JELLYFIN_URL"), value("JELLYFIN_API_KEY"), value("JELLYFIN_USER"))
        elif service == "jellyseerr":
            detail = await setup.test_seerr(value("JELLYSEERR_URL"), value("JELLYSEERR_API_KEY"))
        elif service == "llm":
            provider = value("LLM_PROVIDER") or config.RECOMMENDED_PROVIDER
            names = config.PROVIDER_FIELDS.get(provider, {})
            detail = await setup.test_llm(provider, value(names.get("model", "")) if names else "",
                                          value(names["key"]) if "key" in names else "",
                                          value(names["url"]) if "url" in names else "")
        else:
            return web.json_response({"ok": False, "error": "unknown service"}, status=400)
    except setup.SetupError as e:
        return _setup_error(e)
    return web.json_response({"ok": True, "detail": detail})


def _base_url(request: web.Request) -> str:
    return f"{request.scheme}://{request.host}"


async def api_setup_webhooks(request: web.Request) -> web.Response:
    rt = _rt(request)
    return web.json_response({**setup.webhook_urls(_base_url(request), rt.cfg.webhook_secret),
                              "services": {k: v for k, v in health.snapshot().items()
                                           if k in ("webhook", "sonarr", "radarr")}})


async def api_setup_arr(request: web.Request) -> web.Response:
    """Create the webhook in Sonarr/Radarr. Address and key come from the form, or from the saved access
    (stored only when the user ticked "Remember access" – the assistant saves them through the config API)."""
    rt = _rt(request)
    body = await _json_body(request)
    kind = body.get("kind")
    if kind not in ("sonarr", "radarr"):
        return web.json_response({"ok": False, "error": "unknown service"}, status=400)
    hook = setup.webhook_urls(str(body.get("base") or _base_url(request)), rt.cfg.webhook_secret)[kind]
    try:
        result = await setup.connect_arr(kind, str(body.get("url") or rt.cfg.get(f"{kind.upper()}_URL")),
                                         str(body.get("api_key") or rt.cfg.get(f"{kind.upper()}_API_KEY")), hook)
    except setup.SetupError as e:
        return _setup_error(e)
    log.info(t("log.arr_connected", service=kind.capitalize()))
    return web.json_response({"ok": True, **result})


# --- GUI: backup and restore ------------------------------------------------------

async def api_backup(request: web.Request) -> web.Response:
    """Encrypted zip of database and settings. Only with an admin password, since it holds the API keys."""
    rt = _rt(request)
    if not rt.cfg.admin_password:
        return web.json_response({"error": t("backup.needs_admin_password")}, status=403)
    password = str((await _json_body(request)).get("password") or "")
    try:
        data = await asyncio.to_thread(backup.create, rt.cfg, password)
    except backup.BackupError as e:
        return web.json_response({"error": t(e.key, **e.values)}, status=400)
    log.info(t("log.backup_created", remote=_remote(request)))
    name = f"corsarr-backup-{time.strftime('%Y%m%d-%H%M')}.zip"
    return web.Response(body=data, content_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def api_restore(request: web.Request) -> web.Response:
    """Restore a backup (multipart: file, password). Without an admin password – e.g. on a fresh
    installation – no login is needed; the backup's own password still is."""
    rt = _rt(request)
    form = await request.post()
    upload, password = form.get("file"), str(form.get("password") or "")
    if not isinstance(upload, web.FileField):
        return web.json_response({"error": t("backup.not_a_backup")}, status=400)
    try:
        manifest, settings, database = backup.read(upload.file.read(), password)
    except backup.BackupError as e:
        log.warning(t("log.restore_failed", remote=_remote(request), error=t(e.key, **e.values)))
        return web.json_response({"error": t(e.key, **e.values)}, status=400)
    await rt.restore(settings, database)
    return web.json_response({"ok": True, "version": manifest.get("version", ""),
                              "created": manifest.get("created", ""), **status_payload(rt)})


async def api_events(request: web.Request) -> web.Response:
    try:
        after = int(request.query.get("after", "0"))
    except ValueError:
        after = 0
    return web.json_response({"events": events.since(after), "boot": events.boot, "now": time.time()})


# --- GUI: configuration ---------------------------------------------------------

def config_payload(rt: "Runtime") -> dict:
    cfg = rt.cfg
    fields = []
    for f in config.FIELDS:
        value = cfg.get(f.name)
        fields.append({
            "name": f.name, "group": f.group, "required": f.required, "secret": f.secret,
            "kind": f.kind, "choices": list(f.choices), "editable": f.editable,
            "app_restart": f.app_restart, "default": f.default, "provider": f.provider, "live": f.live,
            "value": "" if f.secret else value, "is_set": bool(value),
            "source": cfg.sources.get(f.name, "default"), "error": cfg.errors.get(f.name, ""),
        })
    settings = rt.db.settings() if rt.db else dict(DEFAULT_SETTINGS)
    behaviour = []
    for key, default in DEFAULT_SETTINGS.items():
        lo, hi = SETTING_LIMITS.get(key, (None, None))
        behaviour.append({"key": key, "value": settings[key], "kind": type(default).__name__,
                          "min": lo, "max": hi})
    return {"fields": fields, "settings": behaviour, "providers": _providers()}


def _providers() -> list[dict]:
    return [{"id": p, "name": config.PROVIDER_NAMES[p], "recommended": p == config.RECOMMENDED_PROVIDER,
             "local": p in config.LOCAL_PROVIDERS, "fields": config.PROVIDER_FIELDS[p]}
            for p in config.PROVIDERS]


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
    needs_restart = any(not config.FIELD_BY_NAME[n].app_restart and not config.FIELD_BY_NAME[n].live
                        and n != "ADMIN_PASSWORD" for n in changed)
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
        if rt.db is None:
            return web.json_response({"errors": {key: rt.db_error}}, status=503)
        rt.db.set_setting(key, value)
        applied[key] = value
    if applied:
        log.info(t("log.settings_changed", changes=applied))
    return web.json_response(config_payload(rt))


# --- app ---------------------------------------------------------------------------

async def _font_headers(request: web.Request, response: web.StreamResponse) -> None:
    # Fonts don't change between versions (app.js/style.css get the version in their URL instead).
    if request.path.startswith("/static/fonts/") and response.status == 200:
        response.headers["Cache-Control"] = "public, max-age=2592000"
        if request.path.endswith(".woff2"):  # unknown to some systems' MIME tables
            response.headers["Content-Type"] = "font/woff2"


def build_app(runtime: "Runtime") -> web.Application:
    # Uploads: backups for restoring can be larger than aiohttp's default of 1 MB.
    app = web.Application(middlewares=[auth_middleware], client_max_size=backup.MAX_SIZE + 1024 * 1024)
    app[RUNTIME] = runtime
    app[SESSIONS] = {}
    app[TASKS] = set()
    app.on_response_prepare.append(_font_headers)
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
    app.router.add_post("/api/options/models", api_models)
    app.router.add_post("/api/options/jellyfin-users", api_jellyfin_users)
    app.router.add_post("/api/update", api_update_start)
    app.router.add_post("/api/setup/telegram", api_setup_telegram)
    app.router.add_post("/api/setup/test", api_setup_test)
    app.router.add_get("/api/setup/webhooks", api_setup_webhooks)
    app.router.add_post("/api/setup/arr", api_setup_arr)
    app.router.add_post("/api/backup", api_backup)
    app.router.add_post("/api/restore", api_restore)
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
