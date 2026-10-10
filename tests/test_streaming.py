"""Streaming services the household subscribes to: Seerr's watch providers, the card line, the pickers."""
import asyncio
import json
from dataclasses import asdict

import httpx

from corsarr import config
from corsarr.bot import CAPTION_LIMIT, CorsarrBot
from corsarr.jellyseerr import Jellyseerr, subscribed
from corsarr.models import Candidate

from test_carousel import make_bot
from test_gui import H, env, login, with_client  # noqa: F401  (env is a fixture)

# Shape of Seerr's movie/tv details field `watchProviders` (server/models/common.ts: mapWatchProviders)
WATCH = [
    {"iso_3166_1": "US", "link": "https://example.org/us",
     "flatrate": [{"id": 15, "name": "Hulu", "displayPriority": 3}], "buy": []},
    {"iso_3166_1": "DE", "link": "https://example.org/de",
     "flatrate": [{"id": 8, "name": "Netflix", "displayPriority": 1, "logoPath": "/n.jpg"},
                  {"id": 337, "name": "Disney Plus", "displayPriority": 2}],
     "buy": [{"id": 2, "name": "Apple TV", "displayPriority": 4}]},
]


def seerr_with(handler) -> Jellyseerr:
    seerr = Jellyseerr("http://seerr:5055", "k")
    seerr.http = httpx.AsyncClient(base_url="http://seerr:5055/api/v1", transport=httpx.MockTransport(handler))
    return seerr


def test_only_subscribed_flatrate_services_of_the_region_count():
    assert subscribed(WATCH, "DE", {8, 337, 15}) == ["Netflix", "Disney Plus"]
    assert subscribed(WATCH, "DE", {337}) == ["Disney Plus"]
    assert subscribed(WATCH, "DE", {2}) == []  # buying is not "included"
    assert subscribed(WATCH, "US", {8, 15}) == ["Hulu"]
    assert subscribed(WATCH, "FR", {8}) == []
    assert subscribed(WATCH, "DE", set()) == [] and subscribed(None, "DE", {8}) == []  # off / no data


def test_enrich_stores_streaming_only_for_new_titles():
    def handler(request):
        return httpx.Response(200, json={"genres": [], "runtime": 100, "watchProviders": WATCH})
    seerr = seerr_with(handler)
    new = Candidate(media_type="movie", source="new", title="Runner", tmdb_id=2)
    lib = Candidate(media_type="movie", source="library", title="John Wick", tmdb_id=1, jellyfin_id="jf1")
    asyncio.run(seerr.enrich([new, lib]))
    assert new.streaming == [] and lib.streaming == []  # feature off until services are chosen
    seerr.set_streaming("DE", {8})
    asyncio.run(seerr.enrich([new, lib]))
    assert new.streaming == ["Netflix"] and lib.streaming == []


def test_regions_and_providers_lists_for_the_picker():
    seen = []

    def handler(request):
        seen.append((request.url.path, request.url.params.get("watchRegion")))
        if request.url.path.endswith("/regions"):
            return httpx.Response(200, json=[{"iso_3166_1": "DE", "english_name": "Germany", "native_name": "Deutschland"},
                                             {"iso_3166_1": "AT", "english_name": "Austria", "native_name": "Österreich"}])
        if request.url.path.endswith("/movies"):
            return httpx.Response(200, json=[{"id": 337, "name": "Disney Plus", "displayPriority": 5},
                                             {"id": 8, "name": "Netflix", "displayPriority": 2, "logoPath": "/n.jpg"}])
        return httpx.Response(200, json=[{"id": 8, "name": "Netflix", "displayPriority": 1},
                                         {"id": 30, "name": "WOW", "displayPriority": 9}])
    seerr = seerr_with(handler)
    regions = asyncio.run(seerr.watch_regions())
    providers = asyncio.run(seerr.watch_providers("DE"))
    assert regions == [{"code": "DE", "name": "Deutschland"}, {"code": "AT", "name": "Österreich"}]
    assert [(p["id"], p["name"]) for p in providers] == [(8, "Netflix"), (337, "Disney Plus"), (30, "WOW")]
    assert providers[0]["logo"] == "https://image.tmdb.org/t/p/w92/n.jpg"
    assert ("/api/v1/watchproviders/movies", "DE") in seen and ("/api/v1/watchproviders/tv", "DE") in seen


def test_caption_names_the_services_and_still_fits():
    bot = CorsarrBot.__new__(CorsarrBot)
    new = Candidate(media_type="movie", source="new", title="Runner", year=2026, overview="Kurier. " * 300,
                    streaming=["Netflix", "Disney Plus"])
    cap = bot._caption(new, "📱 passt")
    lines = cap.split("\n")
    assert lines[1].startswith("🆕") and lines[2] == "📺 Bei Netflix, Disney Plus"
    assert len(cap) <= CAPTION_LIMIT
    assert "📺 Bei" not in bot._caption(Candidate(media_type="movie", source="new", title="X"), "")


def test_cards_saved_before_the_field_existed_still_load(db):
    bot = make_bot(db)
    old = Candidate(media_type="movie", source="new", title="Runner", year=2026, tmdb_id=2, overview="Kurier.")
    data = asdict(old)
    del data["streaming"]  # card JSON written by an older version
    sid = db.add_suggestion(old)
    cid = db.create_carousel([(sid, json.dumps({"candidate": data, "reason": "", "lang": "de"}))])
    row, photo, caption, markup = asyncio.run(bot._page(cid, 0))
    assert "Runner (2026)" in caption and "📺 Bei" not in caption
    assert markup.inline_keyboard[0][0].text == "📥 Anfragen"  # requesting stays possible


def test_streaming_settings_are_validated_and_region_follows_language(env):  # noqa: F811
    cfg = config.load()
    assert cfg.streaming_region == "US" and cfg.streaming_ids == set()  # English by default; no services = off
    env.setenv("LANGUAGE", "de")
    assert config.load().streaming_region == "DE"
    env.setenv("STREAMING_REGION", "at")
    env.setenv("STREAMING_PROVIDERS", "8, 337")
    cfg = config.load()
    assert cfg.streaming_region == "AT" and cfg.streaming_ids == {8, 337} and cfg.complete
    env.setenv("STREAMING_PROVIDERS", "Netflix")
    env.setenv("STREAMING_REGION", "Germany")
    assert set(config.load().errors) == {"STREAMING_PROVIDERS", "STREAMING_REGION"}


def test_streaming_picker_api_and_live_save(env):  # noqa: F811
    from aiohttp import web as aioweb
    from aiohttp.test_utils import TestServer
    keys = []

    async def regions(request):
        keys.append(request.headers.get("X-Api-Key"))
        return aioweb.json_response([{"iso_3166_1": "DE", "english_name": "Germany", "native_name": "Deutschland"}])

    async def providers(request):
        assert request.query["watchRegion"] == "DE"
        return aioweb.json_response([{"id": 8, "name": "Netflix", "displayPriority": 1}])

    async def test(client, rt):
        fake = aioweb.Application()
        fake.router.add_get("/api/v1/watchproviders/regions", regions)
        fake.router.add_get("/api/v1/watchproviders/movies", providers)
        fake.router.add_get("/api/v1/watchproviders/tv", providers)
        server = TestServer(fake)
        await server.start_server()
        try:
            await login(client)
            r = await client.post("/api/options/streaming", headers=H,
                                  json={"url": str(server.make_url("")).rstrip("/"), "api_key": "typed", "region": "de"})
            assert (await r.json()) == {"regions": [{"code": "DE", "name": "Deutschland"}],
                                        "providers": [{"id": 8, "name": "Netflix", "logo": None}],
                                        "region": "DE", "error": ""}
            assert keys == ["typed"]  # the key typed into the form, not the saved one
        finally:
            await server.close()
        bad = await (await client.post("/api/options/streaming", headers=H,
                                       json={"url": "http://127.0.0.1:9", "api_key": "x"})).json()
        assert bad["providers"] == [] and bad["error"]  # the form falls back to text fields

        rt.seerr = Jellyseerr("http://seerr:5055", "k")
        try:
            r = await client.put("/api/config", headers=H, json={"values": {"STREAMING_PROVIDERS": "8,337"}})
            body = await r.json()
            assert r.status == 200 and not body["restart"] and rt.restarts == 0  # no bot restart
            assert rt.seerr.streaming_ids == {8, 337} and rt.seerr.streaming_region == rt.cfg.streaming_region
            r = await client.put("/api/config", headers=H, json={"values": {"STREAMING_PROVIDERS": "Netflix"}})
            assert r.status == 400 and "STREAMING_PROVIDERS" in (await r.json())["errors"]
        finally:
            await rt.seerr.close()
    with_client(test)
