"""Sonarr/Radarr download notifications: bundling, the webhook, and catching up missed imports."""
import asyncio
import json
from datetime import timedelta

from aiohttp.test_utils import TestClient, TestServer

from corsarr import arr, web
from corsarr.db import iso, now
from corsarr.models import Candidate
from corsarr.monitor import health
from helpers import age_imports, episodes, sonarr


def test_backfill_becomes_one_message_after_it_settles(db):
    for s in (1, 2, 3, 4):  # season packs arrive file by file
        arr.record_sonarr(db, sonarr(episodes([s], 12, "2013-01-01T01:00:00Z")))
    assert arr.due_messages(db) == []  # still downloading
    age_imports(db, 5)
    assert arr.due_messages(db) == []  # backfill waits longer than a fresh episode
    age_imports(db, 16)
    [(ids, text)] = arr.due_messages(db)
    assert len(ids) == 48 and text == "📦 Hope's Ward (2005): 48 Folgen aus Staffel 1–4 sind jetzt da"
    db.mark_imports_notified(ids)
    assert arr.due_messages(db) == []


def test_fresh_episode_is_named_quickly(db):
    aired = iso(now() - timedelta(days=1)).replace("+00:00", "Z")
    arr.record_sonarr(db, sonarr([{"seasonNumber": 2, "episodeNumber": 3, "title": "Lanterns", "airDateUtc": aired}],
                                 title="Starfall", year=2022))
    age_imports(db, 3)
    [(_, text)] = arr.due_messages(db)
    assert text == "📺 Starfall (2022): S02E03 „Lanterns“ ist da"


def test_upgrades_and_tests_are_ignored(db):
    assert arr.record_sonarr(db, sonarr(episodes([1], 3, "2013-01-01T01:00:00Z"), upgrade=True)) == 0
    assert arr.record_sonarr(db, {"eventType": "Test"}) == 0
    assert arr.record_radarr(db, {"eventType": "Grab", "movie": {"id": 1, "title": "X"}}) == 0
    assert db.pending_imports() == []


def test_movie_mentions_request_through_the_bot(db):
    c = Candidate(media_type="movie", source="new", title="Hollow Creek", year=2025, tmdb_id=900004)
    db.set_suggestion_status(db.add_suggestion(c), "requested")
    arr.record_radarr(db, {"eventType": "Download", "isUpgrade": False,
                           "movie": {"id": 3, "title": "Hollow Creek", "year": 2025, "tmdbId": 900004}})
    age_imports(db, 2)
    [(_, text)] = arr.due_messages(db)
    assert text == "🎬 Hollow Creek (2025) ist jetzt da – angefragt über Corsarr 📥"


def test_season_ranges():
    assert arr._ranges([1, 2, 3, 5, 7, 8]) == "1–3, 5, 7–8"
    assert arr._ranges([4]) == "4"


def test_sonarr_webhook_needs_secret_and_stores(db, tmp_path, monkeypatch):
    class Cfg:
        webhook_secret = "hook"
        admin_password = ""

    class Runtime:
        cfg = Cfg()

    rt = Runtime()
    rt.db = db

    async def go():
        client = TestClient(TestServer(web.build_app(rt)))
        await client.start_server()
        try:
            body = json.dumps(sonarr(episodes([1], 2, "2013-01-01T01:00:00Z")))
            assert (await client.post("/sonarr?secret=nope", data=body)).status == 403
            assert (await client.post("/sonarr?secret=hook", data=body)).status == 200
        finally:
            await client.close()

    asyncio.run(go())
    assert len(db.pending_imports()) == 2 and health.services["sonarr"].status == "ok"


# --- catching up imports whose webhook never arrived --------------------------------------------

def history(records, seen=None):
    import httpx

    def handler(request):
        if seen is not None:
            seen.append(dict(request.url.params))
        return httpx.Response(200, json=records)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


HARBOR_LANE = {"id": 7, "title": "Harbor Lane", "year": 2025, "tmdbId": 900001}


def imported(ep_id, season, number, aired, event="downloadFolderImported", reason=None):
    rec = {"eventType": event, "episodeId": ep_id, "series": HARBOR_LANE,
           "episode": {"id": ep_id, "seasonNumber": season, "episodeNumber": number, "title": f"E{number}",
                       "airDateUtc": aired}}
    if reason:
        rec["data"] = {"reason": reason}
    return rec


def season(sonarr_series, eps, tmdb=900001, number=2, seen=None):
    """arr.season_state against a fake Sonarr with these series and episodes."""
    import httpx

    def handler(request):
        if seen is not None:
            seen.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=sonarr_series if request.url.path.endswith("/series") else eps)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await arr.season_state("http://sonarr:8989/", "key", tmdb, number, client=client)
    return asyncio.run(go())


def ep(number, days, has_file, monitored=True):
    """An episode of season 2, aired `days` ago (negative: still to come; None: no date yet)."""
    aired = None if days is None else iso(now() - timedelta(days=days)).replace("+00:00", "Z")
    return {"seasonNumber": 2, "episodeNumber": number, "airDateUtc": aired, "hasFile": has_file,
            "monitored": monitored}


def test_season_still_running_in_sonarr_is_not_complete():
    # Harbor Lane season 2: 5 of 10 episodes aired and downloaded – Jellyfin only shows these 5
    eps = [ep(n, 35 - 7 * n, True) for n in range(1, 6)] + [ep(n, -7 * (n - 5), False) for n in range(6, 11)]
    seen = []
    state = season([{**HARBOR_LANE, "status": "continuing"}], eps, seen=seen)
    assert state == {"complete": False, "ended": False}
    assert seen[1] == ("/api/v3/episode", {"seriesId": "7", "seasonNumber": "2"})


def test_season_complete_only_when_everything_aired_and_downloaded():
    done = [ep(n, 60 - n, True) for n in range(1, 4)]
    assert season([{**HARBOR_LANE, "status": "ended"}], done) == {"complete": True, "ended": True}
    assert season([{**HARBOR_LANE, "status": "continuing"}], done) == {"complete": True, "ended": False}
    # aired but not downloaded yet: not finished either
    assert season([HARBOR_LANE], done + [ep(4, 2, False)])["complete"] is False
    # announced without a date: still to come
    assert season([HARBOR_LANE], done + [ep(4, None, False)])["complete"] is False
    # an episode nobody wants (unmonitored, no file) doesn't hold the season open
    assert season([HARBOR_LANE], done + [ep(4, 30, False, monitored=False)])["complete"] is True


def test_season_unknown_to_sonarr_decides_nothing():
    assert season([HARBOR_LANE], [], tmdb=999) is None              # series not in Sonarr
    assert season([HARBOR_LANE], []) is None                        # no such season in Sonarr
    assert season([HARBOR_LANE], [ep(1, 9, True)], number=None) is None


def catch_up(db, kind, records, seen=None):
    async def go():
        async with history(records, seen) as client:
            return await arr.catch_up(db, kind, "http://sonarr:8989/", "key", client=client)
    return asyncio.run(go())


def test_catch_up_records_missed_imports_once_and_skips_upgrades(db):
    aired = iso(now() - timedelta(days=1))
    # S02E01 came by webhook; S02E02 was missed; S01E05 was an upgrade of an existing file.
    arr.record_sonarr(db, {"eventType": "Download", "series": HARBOR_LANE,
                           "episodes": [{"id": 1, "seasonNumber": 2, "episodeNumber": 1, "airDateUtc": aired}]})
    records = [imported(1, 2, 1, aired), imported(2, 2, 2, aired),
               imported(3, 1, 5, aired), imported(3, 1, 5, aired, event="episodeFileDeleted", reason="Upgrade"),
               {"eventType": "grabbed", "episodeId": 9}]
    seen = []
    assert catch_up(db, "sonarr", records, seen) == 1
    assert seen[0]["includeSeries"] == "true" and seen[0]["date"].endswith("Z")
    pending = [(r["season"], r["episode"], r["fresh"]) for r in db.pending_imports()]
    assert pending == [(2, 1, 1), (2, 2, 1)]  # the missed one is a fresh episode like the webhook's
    assert catch_up(db, "sonarr", records) == 0  # next run: nothing twice
    assert db.get_state("arr_cursor:sonarr")


def test_catch_up_movies(db):
    movie = {"id": 3, "title": "Hollow Creek", "year": 2025, "tmdbId": 900004}
    records = [{"eventType": "downloadFolderImported", "movieId": 3, "movie": movie}]
    assert catch_up(db, "radarr", records) == 1
    assert catch_up(db, "radarr", records) == 0
    assert [r["title"] for r in db.pending_imports()] == ["Hollow Creek (2025)"]
