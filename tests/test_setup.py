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
