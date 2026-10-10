"""Configuration, i18n completeness and the web interface (login, CSRF, config API, backups, pickers)."""
import asyncio
import json
import re
import string
import time

from aiohttp.test_utils import TestServer

from corsarr import config, i18n, web
from corsarr.monitor import health
from helpers import H, login, with_client


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


def _dynamic_key_patterns(py_sources, js):
    """Regexes for keys the code builds at run time: t(f"prompt.char_{cid}"), T["svc_" + name],
    T["wiz_" + name + "_title"] – derived from the code, so a new pattern is found, not listed by hand."""
    patterns = []
    for src in py_sources:  # t(f"bot.btn_{rating}") -> bot.btn_[a-z_]+
        for stem in re.findall(r"\bt\(\s*f[\"']([a-z_]+\.[a-z_]*)\{", src):
            patterns.append(re.compile(re.escape(stem) + r"[A-Za-z0-9_]+"))
    for expr in re.findall(r"\bT\[((?:\"[a-z_]*\"|[^\"\]]+?)(?:\s*\+\s*(?:\"[a-z_]*\"|[^\"\]+]+?))*)\]", js):
        pieces = [p.strip() for p in expr.split("+")]
        quoted = [p.startswith('"') for p in pieces]
        if all(quoted) or not any(quoted):
            continue  # a plain literal (counted with the others), or tr()'s own T[key] lookup
        regex = "".join(re.escape(p.strip('"')) if p.startswith('"') else r"[A-Za-z0-9_]+" for p in pieces)
        patterns.append(re.compile("gui\\." + regex))
    assert len(patterns) >= 10, patterns  # the patterns really find the dynamic uses
    return patterns


def test_every_defined_i18n_key_is_used():
    """The reverse of the test above: no leftover texts. Besides literal uses, keys may be built at run time
    from a prefix the code names (see _dynamic_key_patterns)."""
    import pathlib
    root = pathlib.Path(i18n.__file__).parent
    py_sources = [p.read_text(encoding="utf-8") for p in root.glob("*.py") if p.name != "i18n.py"]
    js = (root / "web" / "app.js").read_text(encoding="utf-8")
    literal = set()
    for src in py_sources:  # t("x.y"), SetupError("setup.x"), BackupError("backup.x"), dict values …
        literal |= set(re.findall(r"[\"']([a-z_]+\.[A-Za-z0-9_]+)[\"']", src))
    # T.name, T["name"], tr("name", …) and tr(cond ? "name" : "other", …)
    js_literal = re.compile(r"\bT\.([a-z0-9_]+)|\bT\[\"([a-z0-9_]+)\"\]|\btr\(\s*\"([a-z0-9_]+)\""
                            r"|\btr\(\s*[^\"\n]+\?\s*\"([a-z0-9_]+)\"\s*:\s*\"([a-z0-9_]+)\"")
    literal |= {"gui." + m for groups in js_literal.findall(js) for m in groups if m}
    patterns = _dynamic_key_patterns(py_sources, js)
    unused = {k for k in i18n.EN if k not in literal and not any(p.fullmatch(k) for p in patterns)}
    assert not unused, sorted(unused)


def test_language_switch():
    i18n.set_language("de")
    assert i18n.t("bot.btn_request") == "📥 Anfragen"
    i18n.set_language("xx")  # unknown falls back to English
    assert i18n.t("bot.btn_request") == "📥 Request"


def test_english_is_the_default(env):
    env.delenv("LANGUAGE", raising=False)
    cfg = config.load()
    assert cfg.language == "en" and i18n.default_language() == "en"


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


def test_bad_stored_numbers_fall_back_to_defaults_instead_of_crashing(env, caplog):
    env.setenv("WEBHOOK_PORT", "eighty")
    env.setenv("TELEGRAM_CHAT_ID", "-100x")
    cfg = config.load()
    with caplog.at_level("WARNING", logger="corsarr.config"):
        assert cfg.webhook_port == 8787 and cfg.chat_id == 0 and cfg.notify_chat_id == 0
    assert "WEBHOOK_PORT" in caplog.text and "TELEGRAM_CHAT_ID" in caplog.text
    assert {"WEBHOOK_PORT", "TELEGRAM_CHAT_ID"} <= set(cfg.errors)  # still reported as errors
    env.setenv("WEBHOOK_PORT", "70000")
    assert config.load().webhook_port == 8787


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
        assert rt.cfg.get("ANTHROPIC_API_KEY") == "sk-test" and i18n.language() == "en"
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


def test_malformed_bodies_are_rejected_not_crashed(env):
    async def test(client, rt):
        await login(client)
        for path, method in (("/api/config", client.put), ("/api/settings", client.put), ("/api/login", client.post)):
            assert (await method(path, headers=H, data="not json")).status == 400, path
            assert (await method(path, headers=H, json=[1, 2])).status == 400, path
        r = await client.put("/api/config", headers=H, json={"values": [1], "reset": "x"})
        assert r.status == 200  # wrong shapes inside count as "nothing to change"
    with_client(test)


def test_failed_logins_wait_for_each_other(env, monkeypatch):
    monkeypatch.setattr(web, "LOGIN_DELAY", 0.2)

    async def test(client, rt):
        started = time.monotonic()
        results = await asyncio.gather(login(client, "wrong"), login(client, "wrong"), login(client, "wrong"))
        assert all(r.status == 403 for r in results)
        assert time.monotonic() - started >= 0.6  # three attempts in parallel still cost three delays
    with_client(test)


def test_unknown_host_names_are_refused(env):
    assert web.host_allowed("127.0.0.1:8787", config.load()) and web.host_allowed("[::1]:8787", config.load())
    assert web.host_allowed("::1", config.load()) and web.host_allowed("LOCALHOST", config.load())
    assert web.host_allowed("nas.lan", config.load()) and web.host_allowed("pi.local:8787", config.load())
    assert not web.host_allowed("corsarr.example.org", config.load())
    assert web.host_allowed("corsarr:8787", config.load())
    assert not web.host_allowed("corsarr.example.org.", config.load())
    assert not web.host_allowed("", config.load()) and not web.host_allowed("[::1", config.load())
    env.setenv("ALLOWED_HOSTS", "corsarr.example.org, Other.Example.org")
    assert web.host_allowed("Corsarr.Example.org:8787", config.load())
    env.setenv("WEBHOOK_HOST", "media.example.org")
    assert web.host_allowed("media.example.org", config.load())

    async def test(client, rt):
        bad = {"Host": "evil.example.com"}
        r = await client.get("/", headers=bad)
        assert r.status == 403 and "evil.example.com" in await r.text()  # a page that explains what to do
        assert (await client.get("/api/i18n", headers=bad)).status == 403
        # The webhooks check their secret and /health tells nothing: they answer under any name.
        assert (await client.get("/health", headers=bad)).status == 200
        r = await client.post("/jellyfin", headers={"X-Corsarr-Secret": "hook", **bad}, data="{}")
        assert r.status != 403
        assert (await client.get("/health", headers={"Host": "corsarr.lan"})).status == 200
        await login(client)
        r = await client.put("/api/config", headers=H, json={"values": {"ALLOWED_HOSTS": "evil.example.com"}})
        assert r.status == 200 and not (await r.json())["restart"]  # applies without a restart
        assert (await client.get("/", headers=bad)).status == 200
    with_client(test)


def test_a_refused_name_can_be_allowed_from_the_status_page(env):
    async def test(client, rt):
        other = {"Host": "media.example.org:8787"}
        assert (await client.get("/", headers=other)).status == 403
        await login(client)
        status = await (await client.get("/api/status")).json()
        assert status["blocked_hosts"] == ["media.example.org"]
        # Only names that were actually refused, and only from an address that works.
        assert (await client.post("/api/hosts", headers=H, json={"host": "x.example.org", "allow": True})).status == 400
        assert (await client.post("/api/hosts", headers={**H, **other},
                                  json={"host": "media.example.org", "allow": True})).status == 403
        r = await client.post("/api/hosts", headers=H, json={"host": "media.example.org", "allow": True})
        assert r.status == 200 and (await r.json())["blocked_hosts"] == []
        assert "media.example.org" in config.read_overrides(rt.cfg.data_dir)["ALLOWED_HOSTS"]
        assert (await client.get("/", headers=other)).status == 200
        # Dismissing only forgets the name.
        assert (await client.get("/", headers={"Host": "nope.example.org"})).status == 403
        await client.post("/api/hosts", headers=H, json={"host": "nope.example.org", "allow": False})
        assert (await (await client.get("/api/status")).json())["blocked_hosts"] == []
        assert "nope" not in config.read_overrides(rt.cfg.data_dir)["ALLOWED_HOSTS"]
    with_client(test)


def test_saved_keys_only_go_to_the_saved_address(env, monkeypatch):
    from corsarr import llm, setup
    calls = []

    async def fake_models(provider, key, url=""):
        calls.append(("models", key, url))
        return []
    monkeypatch.setattr(llm, "available_models", fake_models)

    async def fake_test(url, key):
        calls.append(("seerr", key, url))
        return "ok"
    monkeypatch.setattr(setup, "test_seerr", fake_test)

    async def fake_arr(kind, url, key, hook):
        calls.append((kind, key, url))
        return {}
    monkeypatch.setattr(setup, "connect_arr", fake_arr)
    env.setenv("SONARR_URL", "http://sonarr:8989")
    env.setenv("SONARR_API_KEY", "sonarrkey")

    async def test(client, rt):
        await login(client)
        # Only the address typed in: the saved key must not be sent there.
        r = await (await client.post("/api/options/models", headers=H,
                                     json={"provider": "claude", "url": "http://attacker:1"})).json()
        assert r["error"] == i18n.t("gui.options_need_key")
        r = await (await client.post("/api/options/jellyfin-users", headers=H,
                                     json={"url": "http://attacker:1"})).json()
        assert r["users"] == [] and r["error"] == i18n.t("gui.options_need_jellyfin")
        r = await (await client.post("/api/options/streaming", headers=H, json={"url": "http://attacker:1"})).json()
        assert r["error"] == i18n.t("gui.options_need_seerr")
        r = await client.post("/api/setup/test", headers=H,
                              json={"service": "jellyseerr", "values": {"JELLYSEERR_URL": "http://attacker:1"}})
        assert r.status == 400 and "JELLYSEERR_API_KEY" in (await r.json())["error"]
        r = await client.post("/api/setup/arr", headers=H, json={"kind": "sonarr", "url": "http://attacker:1"})
        assert r.status == 400 and "SONARR_API_KEY" in (await r.json())["error"]
        assert calls == []
        # The saved address (also with a trailing slash) or none at all: the saved key is fine.
        await client.post("/api/options/models", headers=H, json={"provider": "claude"})
        await client.post("/api/setup/test", headers=H,
                          json={"service": "jellyseerr", "values": {"JELLYSEERR_URL": "http://seerr:5055/"}})
        await client.post("/api/setup/arr", headers=H, json={"kind": "sonarr", "url": "http://sonarr:8989/"})
        # A typed key goes wherever the form says.
        await client.post("/api/setup/arr", headers=H,
                          json={"kind": "sonarr", "url": "http://new:8989", "api_key": "typed"})
        assert calls == [("models", "sk-test", ""), ("seerr", "seerrkey", "http://seerr:5055/"),
                         ("sonarr", "sonarrkey", "http://sonarr:8989/"), ("sonarr", "typed", "http://new:8989")]
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


def test_update_api_only_triggers_on_service_installs(env, tmp_path, monkeypatch):
    from corsarr import updates

    checked = []

    async def fake_check(channel, force=False):
        checked.append(channel)
        downgrade = channel == "beta"  # pretend beta → stable … just to exercise the confirmation
        return {"channel": channel, "current": "v1.0.0", "latest": "v1.1.0", "target": "v1.1.0", "behind": 1,
                "commits": [], "releases": [], "downgrade": downgrade, "error": ""}
    monkeypatch.setattr(updates, "check", fake_check)
    monkeypatch.delenv("CORSARR_UPDATE_TRIGGER", raising=False)
    trigger = tmp_path / "update-requested"

    async def test(client, rt):
        await login(client)
        r = await client.get("/api/update")
        info = await r.json()
        assert r.status == 200, info
        assert info["behind"] == 1 and info["kind"] in ("manual", "docker")
        assert (await client.post("/api/update", headers=H)).status == 400  # can't update itself
        monkeypatch.setenv("CORSARR_UPDATE_TRIGGER", str(trigger))
        assert (await client.post("/api/update")).status == 403  # CSRF header still required
        assert (await client.post("/api/update", headers=H)).status == 200
        assert trigger.read_text().strip() == "v1.1.0" and checked[-1] == "stable"
        assert (await (await client.get("/api/update")).json())["updating"] is True
        trigger.unlink()
        await client.put("/api/config", headers=H, json={"values": {"UPDATE_CHANNEL": "beta"}})
        assert rt.state != "starting"  # the channel takes effect without restarting the bot
        assert (await client.post("/api/update", headers=H)).status == 409  # downgrade needs a confirmation
        assert (await client.post("/api/update", headers=H, json={"downgrade": True})).status == 200
    with_client(test)


def test_jellyfin_user_picker_reads_accounts_with_unsaved_values(env):
    from aiohttp import web as aioweb
    seen = []

    async def users(request):
        seen.append(request.headers.get("Authorization", ""))
        return aioweb.json_response([{"Name": "LivingRoom"}, {"Name": "Kids"}])

    async def test(client, rt):
        fake = aioweb.Application()
        fake.router.add_get("/Users", users)
        jf = TestServer(fake)
        await jf.start_server()
        try:
            await login(client)
            r = await client.post("/api/options/jellyfin-users", headers=H,
                                  json={"url": str(jf.make_url("")), "api_key": "typed-key"})
            assert (await r.json()) == {"users": ["Kids", "LivingRoom"], "error": ""}
            assert 'Token="typed-key"' in seen[0]  # the key typed into the form, not the saved one
        finally:
            await jf.close()
        bad = await (await client.post("/api/options/jellyfin-users", headers=H,
                                       json={"url": "http://127.0.0.1:9", "api_key": "x"})).json()
        assert bad["users"] == [] and bad["error"]  # the form falls back to a text field
    with_client(test)


def test_claude_model_picker_marks_haiku_and_prices(env, monkeypatch):
    from corsarr import llm
    used = []

    async def fake_models(provider, key, url=""):
        used.append((provider, key))
        return [{"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5", "recommended": True,
                 "cost": llm.model_cost("claude-haiku-5-5")},
                {"id": "claude-fable-5-1", "name": "Claude Fable 5.1", "recommended": False,
                 "cost": llm.model_cost("claude-fable-5-1")}]
    monkeypatch.setattr(llm, "available_models", fake_models)

    async def test(client, rt):
        await login(client)
        res = await (await client.post("/api/options/models", headers=H, json={})).json()
        assert used == [("claude", "sk-test")] and res["recommended"] == "claude-haiku-5-5"
        fable = res["models"][1]["cost"]
        assert fable["factor"] == 100 and fable["per_suggestion"] == 0.2
    with_client(test)


def test_interface_assets_carry_the_version_so_updates_are_not_cached(env, monkeypatch):
    from corsarr import updates
    monkeypatch.setattr(updates, "current_version", lambda: "abcdef1234567890")

    async def test(client, rt):
        html = await (await client.get("/")).text()
        assert '/static/app.js?v=abcdef123456"' in html and '/static/style.css?v=abcdef123456"' in html
        await login(client)
        assert (await (await client.get("/api/status")).json())["version"] == "abcdef1234567890"
    with_client(test)


def test_backup_round_trip_needs_the_backups_own_password(env):
    from aiohttp import FormData

    def upload(data, password):
        form = FormData()
        form.add_field("file", data, filename="backup.zip", content_type="application/zip")
        form.add_field("password", password)
        return form

    async def test(client, rt):
        await login(client)
        await client.put("/api/settings", headers=H, json={"pirate_enabled": False})
        assert (await client.post("/api/backup", headers=H, json={"password": "short"})).status == 400
        r = await client.post("/api/backup", headers=H, json={"password": "backup-secret"})
        assert r.status == 200 and r.headers["Content-Disposition"].startswith("attachment")
        data = await r.read()
        assert data[:2] == b"PK" and b"sk-test" not in data  # encrypted: the API key is not readable

        # change things after the backup – restoring must bring the old state back
        await client.put("/api/settings", headers=H, json={"pirate_enabled": True})
        await client.put("/api/config", headers=H, json={"values": {"JELLYFIN_USER": "Changed"}})
        r = await client.post("/api/restore", headers=H, data=upload(data, "pw"))  # admin password ≠ backup password
        assert r.status == 400 and "password" in (await r.json())["error"].lower()
        r = await client.post("/api/restore", headers=H, data=upload(b"PK not a zip", "backup-secret"))
        assert r.status == 400
        r = await client.post("/api/restore", headers=H, data=upload(data, "backup-secret"))
        assert r.status == 200, await r.text()
        assert rt.db.settings()["pirate_enabled"] is False and rt.cfg.jellyfin_user == "Wohnzimmer"
        assert rt.cfg.sources["ANTHROPIC_API_KEY"] == "gui"  # values from the environment are in the backup too
        assert any((rt.cfg.data_dir / "backups").glob("pre-restore-*/corsarr.db"))  # old data kept
    with_client(test)


def test_backup_needs_an_admin_password(env):
    env.delenv("ADMIN_PASSWORD")

    async def test(client, rt):
        r = await client.post("/api/backup", headers=H, json={"password": "backup-secret"})
        assert r.status == 403
    with_client(test)


def test_bundled_fonts_are_served_cached_and_small(env):
    import pathlib
    import re as _re
    root = pathlib.Path(web.STATIC)
    css = (root / "style.css").read_text(encoding="utf-8")
    urls = _re.findall(r'url\("(fonts/[^"]+)"\)', css)
    assert len(urls) == 4 and all((root / u).is_file() for u in urls)
    assert all((root / "fonts" / name / "OFL.txt").is_file() for name in ("vt323", "nunito"))
    assert sum(p.stat().st_size for p in (root / "fonts").rglob("*")) < 400_000
    assert "http" not in "".join(urls)  # nothing from outside at runtime

    async def test(client, rt):
        r = await client.get("/static/" + urls[0])
        assert r.status == 200 and "max-age" in r.headers["Cache-Control"]
        assert r.headers["Content-Type"] == "font/woff2"
        assert "Cache-Control" not in (await client.get("/static/app.js")).headers
        await login(client)
        data = await (await client.get("/api/events")).json()
        assert data["boot"]  # the GUI notices a restart by this value
    with_client(test)
