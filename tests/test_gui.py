import asyncio
import json
import re
import string

import pytest
from aiohttp.test_utils import TestClient, TestServer

from corsarr import config, i18n, web
from corsarr.db import DB
from corsarr.monitor import health

ENV = {
    "TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-100", "ANTHROPIC_API_KEY": "sk-test",
    "JELLYFIN_URL": "http://jf:8096", "JELLYFIN_API_KEY": "jfkey", "JELLYFIN_USER": "Wohnzimmer",
    "JELLYSEERR_URL": "http://seerr:5055", "JELLYSEERR_API_KEY": "seerrkey", "WEBHOOK_SECRET": "hook",
    "ADMIN_PASSWORD": "pw",
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for f in config.FIELDS:
        monkeypatch.delenv(f.name, raising=False)
    monkeypatch.setenv("CORSARR_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    yield monkeypatch
    i18n.set_language("de")
    health.reset()


# --- i18n ----------------------------------------------------------------------------

def _placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_text_exists_in_both_languages_with_same_placeholders():
    assert i18n.DE.keys() == i18n.EN.keys()
    for key in i18n.DE:
        assert _placeholders(i18n.DE[key]) == _placeholders(i18n.EN[key]), key


def test_every_i18n_key_used_in_code_exists():
    import pathlib
    root = pathlib.Path(i18n.__file__).parent
    py_call = re.compile(r"\bt\(\s*f?[\"']([a-z_]+\.[A-Za-z_]+)[\"']")
    js_use = re.compile(r"\bT\.([a-z_]+)|\btr\(\"([a-z_]+)\"")
    used = set()
    for path in root.glob("*.py"):
        used |= set(py_call.findall(path.read_text(encoding="utf-8")))
    js = (root / "web" / "app.js").read_text(encoding="utf-8")
    used |= {"gui." + (a or b) for a, b in js_use.findall(js)}
    assert len(used) > 150  # the patterns really find the calls
    missing = {k for k in used if k not in i18n.DE}
    assert not missing


def test_language_switch():
    i18n.set_language("en")
    assert i18n.t("bot.btn_request") == "📥 Request"
    i18n.set_language("xx")  # unknown falls back to German
    assert i18n.t("bot.btn_request") == "📥 Anfragen"


# --- config --------------------------------------------------------------------------

def test_config_precedence_gui_over_env_over_default(env, tmp_path):
    env_file = tmp_path / "x.env"
    env_file.write_text("CLAUDE_MODEL=from-dotenv\nJELLYFIN_USER=dotenv-user\n", encoding="utf-8")
    env.setenv("CORSARR_ENV_FILE", str(env_file))
    cfg = config.load()
    assert cfg.model == "from-dotenv" and cfg.sources["CLAUDE_MODEL"] == "env"
    assert cfg.jellyfin_user == "Wohnzimmer"  # real environment beats .env
    assert cfg.get("WEBHOOK_HOST") == "0.0.0.0" and cfg.sources["WEBHOOK_HOST"] == "default"
    config.write_overrides(cfg.data_dir, {"JELLYFIN_USER": "gui-user", "DATA_DIR": "/elsewhere"})
    cfg = config.load()
    assert cfg.jellyfin_user == "gui-user" and cfg.sources["JELLYFIN_USER"] == "gui"
    assert cfg.data_dir == (tmp_path / "data").resolve()  # DATA_DIR is never taken from the GUI
    assert cfg.complete


def test_config_reports_missing_and_invalid_without_exiting(env):
    env.delenv("TELEGRAM_BOT_TOKEN")
    env.setenv("TELEGRAM_CHAT_ID", "-100...")
    env.setenv("JELLYFIN_URL", "jf:8096")
    env.setenv("LANGUAGE", "en")
    cfg = config.load()
    assert not cfg.complete
    assert set(cfg.errors) == {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "JELLYFIN_URL"}
    assert "must be a number" in cfg.errors["TELEGRAM_CHAT_ID"]  # already in the configured language


# --- web ---------------------------------------------------------------------------------

class FakeRuntime:
    def __init__(self):
        self.cfg = config.load()
        self.db = DB(self.cfg.db_path)
        self.state, self.state_detail = "running", ""
        self.started_at = 0.0
        self.corsarr = None
        self.feedback = None
        self.restarts = 0
        self.checks = 0

    async def restart(self):
        self.restarts += 1
        self.cfg = config.load()

    async def check(self):
        self.checks += 1


def with_client(test):
    """Run `test(client, runtime)` against the real web app with a fake runtime."""
    async def go():
        rt = FakeRuntime()
        client = TestClient(TestServer(web.build_app(rt)))
        await client.start_server()
        try:
            await test(client, rt)
        finally:
            await client.close()
    asyncio.run(go())


async def login(client, password="pw"):
    return await client.post("/api/login", json={"password": password})


H = {"X-Corsarr": "1"}


def test_api_requires_login_and_csrf_header(env):
    async def test(client, rt):
        assert (await client.get("/api/status")).status == 401
        assert (await client.get("/api/i18n")).status == 200  # login page needs texts
        assert (await login(client, "wrong")).status == 403
        assert (await login(client)).status == 200
        assert (await client.get("/api/status")).status == 200
        assert (await client.post("/api/check")).status == 403  # no custom header
        assert (await client.post("/api/check", headers=H)).status == 200 and rt.checks == 1
        await client.post("/api/logout", headers=H)
        assert (await client.get("/api/status")).status == 401
    with_client(test)


def test_config_never_returns_secrets_and_keeps_them_when_empty(env):
    async def test(client, rt):
        await login(client)
        data = await (await client.get("/api/config")).json()
        fields = {f["name"]: f for f in data["fields"]}
        assert fields["ANTHROPIC_API_KEY"]["value"] == "" and fields["ANTHROPIC_API_KEY"]["is_set"]
        assert "sk-test" not in json.dumps(data)
        assert fields["JELLYFIN_USER"]["value"] == "Wohnzimmer"

        r = await client.put("/api/config", headers=H, json={"values": {
            "ANTHROPIC_API_KEY": "", "JELLYFIN_USER": "Kino", "LANGUAGE": "en"}})
        body = await r.json()
        assert r.status == 200 and body["restart"] and rt.restarts == 1
        saved = config.read_overrides(rt.cfg.data_dir)
        assert saved == {"JELLYFIN_USER": "Kino", "LANGUAGE": "en"}  # empty secret = unchanged
        assert rt.cfg.anthropic_api_key == "sk-test" and i18n.language() == "en"
    with_client(test)


def test_config_rejects_invalid_values_and_can_reset(env):
    async def test(client, rt):
        await login(client)
        r = await client.put("/api/config", headers=H, json={"values": {"TELEGRAM_CHAT_ID": "abc"}})
        assert r.status == 400 and "TELEGRAM_CHAT_ID" in (await r.json())["errors"]
        await client.put("/api/config", headers=H, json={"values": {"JELLYFIN_USER": "Kino"}})
        r = await client.put("/api/config", headers=H, json={"reset": ["JELLYFIN_USER"]})
        assert r.status == 200 and config.read_overrides(rt.cfg.data_dir) == {}
        assert rt.cfg.jellyfin_user == "Wohnzimmer"
    with_client(test)


def test_without_password_gui_is_open_but_still_needs_csrf_header(env):
    env.delenv("ADMIN_PASSWORD")

    async def test(client, rt):
        r = await client.get("/api/status")
        assert r.status == 200 and (await r.json())["auth"] is False
        assert (await client.post("/api/check")).status == 403  # foreign pages still can't trigger actions
        assert (await client.post("/api/check", headers=H)).status == 200
    with_client(test)


def test_behaviour_settings_are_clamped(env):
    async def test(client, rt):
        await login(client)
        r = await client.put("/api/settings", headers=H, json={"abort_days": 999, "pirate_enabled": False})
        assert r.status == 200
        assert rt.db.settings()["abort_days"] == 60 and rt.db.settings()["pirate_enabled"] is False
    with_client(test)


def test_webhook_checks_secret_and_updates_health(env):
    async def test(client, rt):
        r = await client.post("/jellyfin", headers={"X-Corsarr-Secret": "nope"}, data="{}")
        assert r.status == 403 and health.services["webhook"].status == "error"
        r = await client.post("/jellyfin", headers={"X-Corsarr-Secret": "hook"},
                              data=json.dumps({"event": "PlaybackStart", "itemId": "1"}))
        assert r.status == 200 and health.services["webhook"].status == "ok"
        r = await client.post("/jellyfin", headers={"X-Corsarr-Secret": "hook"},
                              data=json.dumps({"event": "PlaybackStop", "itemId": "1"}))
        assert r.status == 503  # bot not running in this fake runtime
    with_client(test)


def test_database_from_before_the_rename_is_taken_over(env, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "filmbot.db").write_bytes(b"old data")
    (data / "filmbot.db-wal").write_bytes(b"wal")
    config.load()
    assert (data / "corsarr.db").read_bytes() == b"old data" and (data / "corsarr.db-wal").exists()
    assert not (data / "filmbot.db").exists()


def test_old_jellyfin_header_still_accepted(env):
    async def test(client, rt):
        r = await client.post("/jellyfin", headers={"X-Filmbot-Secret": "hook"},
                              data=json.dumps({"event": "PlaybackStart", "itemId": "1"}))
        assert r.status == 200
    with_client(test)
