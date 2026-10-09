"""Setup assistant: Telegram group detection and webhooks in Sonarr/Radarr – against simulated servers."""
import asyncio
import json

import httpx
import pytest

from corsarr import setup


def run(coro_fn, handler):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await coro_fn(client)
    return asyncio.run(go())


def test_telegram_lists_groups_newest_first_and_reports_privacy():
    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        if method == "getMe":
            return httpx.Response(200, json={"ok": True, "result": {"username": "corsarr_bot",
                                                                    "can_read_all_group_messages": False}})
        return httpx.Response(200, json={"ok": True, "result": [
            {"message": {"chat": {"id": 5, "type": "private", "first_name": "Sam"}}},
            {"my_chat_member": {"chat": {"id": -100, "type": "supergroup", "title": "Movie Night"}}},
            {"message": {"chat": {"id": -200, "type": "group", "title": "Family"}}},
            {"message": {"chat": {"id": -100, "type": "supergroup", "title": "Movie Night"}}},
        ]})

    info = run(lambda c: setup.telegram("123:abc", client=c), handler)
    assert info["username"] == "corsarr_bot" and info["privacy"] is True
    assert info["chats"] == [{"id": -100, "title": "Movie Night"}, {"id": -200, "title": "Family"}]


@pytest.mark.parametrize("status,key", [(401, "setup.telegram_token"), (409, "setup.telegram_busy")])
def test_telegram_errors(status, key):
    with pytest.raises(setup.SetupError) as e:
        run(lambda c: setup.telegram("bad", client=c),
            lambda r: httpx.Response(status, json={"ok": False, "description": "x"}))
    assert e.value.key == key


SCHEMA = [{"implementation": "Slack", "fields": []},
          {"implementation": "Webhook", "name": "", "onGrab": False, "onDownload": False, "onUpgrade": False,
           "onHealthIssue": False, "presets": [],
           "fields": [{"name": "url", "value": ""}, {"name": "method", "value": 1}, {"name": "username"}]}]


def arr_server(existing, saved, status=201):
    def handler(request):
        if request.headers.get("X-Api-Key") != "arrkey":
            return httpx.Response(401)
        path = request.url.path
        if path.endswith("/notification/schema"):
            return httpx.Response(200, json=SCHEMA)
        if request.method == "GET":
            return httpx.Response(200, json=existing)
        saved.append((request.method, path, json.loads(request.content)))
        return httpx.Response(status, json=[{"errorMessage": "Unable to send test message"}] if status >= 400 else {})
    return handler


def test_arr_webhook_is_created_with_only_on_import():
    saved = []
    existing = [{"id": 3, "implementation": "Telegram", "name": "Telegram group"}]
    hook = "http://corsarr.lan:8787/sonarr?secret=s"
    res = run(lambda c: setup.connect_arr("sonarr", "http://sonarr:8989/", "arrkey", hook, client=c),
              arr_server(existing, saved))
    assert res == {"updated": False, "telegram": ["Telegram group"]}  # duplicate messages warning
    method, path, body = saved[0]
    assert (method, path) == ("POST", "/api/v3/notification")
    assert body["name"] == "Corsarr" and body["onDownload"] is True
    assert body["onGrab"] is False and body["onUpgrade"] is False and "presets" not in body
    assert {f["name"]: f.get("value") for f in body["fields"]}["url"] == hook


def test_arr_existing_corsarr_webhook_is_updated_not_duplicated():
    saved = []
    existing = [{"id": 7, "implementation": "Webhook", "name": "old", "onDownload": True,
                 "fields": [{"name": "url", "value": "http://old:8787/radarr?secret=x"}, {"name": "method", "value": 2}]}]
    res = run(lambda c: setup.connect_arr("radarr", "http://radarr:7878", "arrkey", "http://new:8787/radarr?secret=s",
                                          client=c), arr_server(existing, saved))
    assert res["updated"] and saved[0][:2] == ("PUT", "/api/v3/notification/7")
    assert {f["name"]: f["value"] for f in saved[0][2]["fields"]} == {"url": "http://new:8787/radarr?secret=s",
                                                                     "method": 1}


def test_arr_errors():
    with pytest.raises(setup.SetupError) as e:
        run(lambda c: setup.connect_arr("sonarr", "http://sonarr:8989", "wrong", "h", client=c), arr_server([], []))
    assert e.value.key == "setup.arr_key"
    with pytest.raises(setup.SetupError) as e:
        run(lambda c: setup.connect_arr("sonarr", "http://sonarr:8989", "arrkey", "h", client=c),
            arr_server([], [], status=400))
    assert e.value.key == "setup.arr_rejected" and "Unable to send" in e.value.values["error"]


def test_webhook_urls_carry_the_secret():
    urls = setup.webhook_urls("http://192.0.2.5:8787/", "s3cret")
    assert urls["jellyfin"] == "http://192.0.2.5:8787/jellyfin"
    assert urls["sonarr"] == "http://192.0.2.5:8787/sonarr?secret=s3cret" and "{{ItemId}}" in urls["template"]


# --- status checks of the webhook senders ---------------------------------------------------------

def jellyfin_server(plugins, conf):
    def handler(request):
        if request.url.path == "/Plugins":
            return httpx.Response(200, json=plugins)
        return httpx.Response(200, json=conf)
    return handler


WEBHOOK_PLUGIN = [{"Name": "Webhook", "Id": "abc", "Version": "18.0.0.0"}]


def dest(uri="http://corsarr:8787/jellyfin", secret="s", types=("PlaybackStop",), **extra):
    return {"WebhookUri": uri, "NotificationTypes": list(types),
            "Headers": [{"Key": "X-Corsarr-Secret", "Value": secret}], **extra}


@pytest.mark.parametrize("plugins,conf,key", [
    ([{"Name": "Other", "Id": "x"}], {}, "check.jf_plugin_missing"),
    (WEBHOOK_PLUGIN, {"GenericOptions": [dest(uri="http://elsewhere/hook")]}, "check.jf_hook_missing"),
    (WEBHOOK_PLUGIN, {"GenericOptions": [dest(secret="old")]}, "check.jf_hook_secret"),
    (WEBHOOK_PLUGIN, {"GenericOptions": [dest(types=("ItemAdded",))]}, "check.jf_hook_type"),
    (WEBHOOK_PLUGIN, {"GenericOptions": [dest(EnableWebhook=False)]}, "check.jf_hook_disabled"),
])
def test_jellyfin_hook_problems_are_named(plugins, conf, key):
    with pytest.raises(setup.SetupError) as e:
        run(lambda c: setup.check_jellyfin_hook("http://jf:8096", "k", "s", client=c), jellyfin_server(plugins, conf))
    assert e.value.key == key


def test_jellyfin_hook_ok():
    conf = {"GenericOptions": [dest(uri="http://elsewhere/x"), dest()]}
    version = run(lambda c: setup.check_jellyfin_hook("http://jf:8096", "k", "s", client=c),
                  jellyfin_server(WEBHOOK_PLUGIN, conf))
    assert version == "18.0.0.0"


def arr_status_server(hooks, tested, test_status=200):
    def handler(request):
        if request.headers.get("X-Api-Key") != "arrkey":
            return httpx.Response(401)
        path = request.url.path
        if path.endswith("/system/status"):
            return httpx.Response(200, json={"version": "5.2.0"})
        if path.endswith("/notification/test"):
            tested.append(json.loads(request.content)["name"])
            return httpx.Response(test_status, json=[{"errorMessage": "Connection refused"}])
        return httpx.Response(200, json=hooks)
    return handler


OUR_HOOK = {"id": 4, "name": "Corsarr", "implementation": "Webhook", "enable": True, "onDownload": True,
            "fields": [{"name": "url", "value": "http://corsarr:8787/radarr?secret=s"}]}


def test_arr_check_finds_the_webhook_and_sends_a_test_only_when_asked():
    tested = []
    check = lambda send: (lambda c: setup.check_arr("radarr", "http://radarr:7878", "arrkey", "s", send_test=send, client=c))
    assert run(check(False), arr_status_server([OUR_HOOK], tested)) == "5.2.0" and tested == []
    run(check(True), arr_status_server([OUR_HOOK], tested))
    assert tested == ["Corsarr"]


@pytest.mark.parametrize("hooks,status,key", [
    ([], 200, "check.arr_hook_missing"),
    ([{**OUR_HOOK, "fields": [{"name": "url", "value": "http://corsarr:8787/radarr?secret=old"}]}], 200, "check.arr_hook_missing"),
    ([{**OUR_HOOK, "onDownload": False}], 200, "check.arr_hook_disabled"),
    ([OUR_HOOK], 400, "check.arr_test_failed"),
])
def test_arr_check_problems(hooks, status, key):
    with pytest.raises(setup.SetupError) as e:
        run(lambda c: setup.check_arr("radarr", "http://radarr:7878", "arrkey", "s", send_test=True, client=c),
            arr_status_server(hooks, [], test_status=status))
    assert e.value.key == key


def test_status_tiles_check_actively_only_with_saved_access(tmp_path, monkeypatch):
    from corsarr import checks, config, i18n
    from corsarr.monitor import health
    for f in config.FIELDS:
        monkeypatch.delenv(f.name, raising=False)
    monkeypatch.setenv("CORSARR_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEBHOOK_SECRET", "s")
    monkeypatch.setenv("LANGUAGE", "en")
    calls = []

    async def fake_check(kind, url, key, secret, send_test=False):
        calls.append((kind, send_test))
        if kind == "sonarr":
            raise setup.SetupError("check.arr_hook_missing")
        return "5.2.0"
    monkeypatch.setattr(setup, "check_arr", fake_check)
    health.reset()
    try:
        cfg = config.load()
        asyncio.run(checks._check_hook(cfg, "radarr", True))
        assert calls == [] and health.services["radarr"].status == "unknown"
        assert "Remember" not in health.services["radarr"].detail and "Setup" in health.services["radarr"].detail

        for k, v in {"SONARR_URL": "http://sonarr:8989", "SONARR_API_KEY": "k",
                     "RADARR_URL": "http://radarr:7878", "RADARR_API_KEY": "k"}.items():
            monkeypatch.setenv(k, v)
        cfg = config.load()
        health.services["radarr"].event = "last: Download"
        asyncio.run(checks._check_hook(cfg, "radarr", True))
        asyncio.run(checks._check_hook(cfg, "sonarr", False))
        assert calls == [("radarr", True), ("sonarr", False)]
        assert health.services["radarr"].status == "ok"
        assert health.services["radarr"].detail == "version 5.2.0, test event sent to Corsarr · last: Download"
        assert health.services["sonarr"].status == "error" and "No Corsarr webhook" in health.services["sonarr"].detail
    finally:
        health.reset()
        i18n.set_language("de")
