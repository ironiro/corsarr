import asyncio
import json
from datetime import timedelta

from aiohttp.test_utils import TestClient, TestServer

from corsarr import arr, web
from corsarr.db import iso, now
from corsarr.models import Candidate
from corsarr.monitor import health


def episodes(seasons, per_season, aired):
    return [{"seasonNumber": s, "episodeNumber": e, "title": f"Folge {e}", "airDateUtc": aired}
            for s in seasons for e in range(1, per_season + 1)]


def sonarr(eps, upgrade=False, series_id=7, title="Grey's Anatomy", year=2005, tmdb=1416):
    return {"eventType": "Download", "isUpgrade": upgrade, "episodes": eps,
            "series": {"id": series_id, "title": title, "year": year, "tmdbId": tmdb}}


def age_imports(db, minutes):
    db.conn.execute("UPDATE arr_imports SET created_at=?", (iso(now() - timedelta(minutes=minutes)),))
    db.conn.commit()


def test_backfill_becomes_one_message_after_it_settles(db):
    for s in (1, 2, 3, 4):  # season packs arrive file by file
        arr.record_sonarr(db, sonarr(episodes([s], 12, "2013-01-01T01:00:00Z")))
    assert arr.due_messages(db) == []  # still downloading
    age_imports(db, 5)
    assert arr.due_messages(db) == []  # backfill waits longer than a fresh episode
    age_imports(db, 16)
    [(ids, text)] = arr.due_messages(db)
    assert len(ids) == 48 and text == "📦 Grey's Anatomy (2005): 48 Folgen aus Staffel 1–4 sind jetzt da"
    db.mark_imports_notified(ids)
    assert arr.due_messages(db) == []


def test_fresh_episode_is_named_quickly(db):
    aired = iso(now() - timedelta(days=1)).replace("+00:00", "Z")
    arr.record_sonarr(db, sonarr([{"seasonNumber": 2, "episodeNumber": 3, "title": "Harvest", "airDateUtc": aired}],
                                 title="Andor", year=2022))
    age_imports(db, 3)
    [(_, text)] = arr.due_messages(db)
    assert text == "📺 Andor (2022): S02E03 „Harvest“ ist da"


def test_upgrades_and_tests_are_ignored(db):
    assert arr.record_sonarr(db, sonarr(episodes([1], 3, "2013-01-01T01:00:00Z"), upgrade=True)) == 0
    assert arr.record_sonarr(db, {"eventType": "Test"}) == 0
    assert arr.record_radarr(db, {"eventType": "Grab", "movie": {"id": 1, "title": "X"}}) == 0
    assert db.pending_imports() == []


def test_movie_mentions_request_through_the_bot(db):
    c = Candidate(media_type="movie", source="new", title="Weapons", year=2025, tmdb_id=1078605)
    db.set_suggestion_status(db.add_suggestion(c), "requested")
    arr.record_radarr(db, {"eventType": "Download", "isUpgrade": False,
                           "movie": {"id": 3, "title": "Weapons", "year": 2025, "tmdbId": 1078605}})
    age_imports(db, 2)
    [(_, text)] = arr.due_messages(db)
    assert text == "🎬 Weapons (2025) ist jetzt da – angefragt über Corsarr 📥"


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
    health.reset()


# --- catching up imports whose webhook never arrived --------------------------------------------

def history(records, seen=None):
    import httpx

    def handler(request):
        if seen is not None:
            seen.append(dict(request.url.params))
        return httpx.Response(200, json=records)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


MOBLAND = {"id": 7, "title": "MobLand", "year": 2025, "tmdbId": 247718}


def imported(ep_id, season, number, aired, event="downloadFolderImported", reason=None):
    rec = {"eventType": event, "episodeId": ep_id, "series": MOBLAND,
           "episode": {"id": ep_id, "seasonNumber": season, "episodeNumber": number, "title": f"E{number}",
                       "airDateUtc": aired}}
    if reason:
        rec["data"] = {"reason": reason}
    return rec


def catch_up(db, kind, records, seen=None):
    async def go():
        async with history(records, seen) as client:
            return await arr.catch_up(db, kind, "http://sonarr:8989/", "key", client=client)
    return asyncio.run(go())


def test_catch_up_records_missed_imports_once_and_skips_upgrades(db):
    aired = iso(now() - timedelta(days=1))
    # S02E01 came by webhook; S02E02 was missed; S01E05 was an upgrade of an existing file.
    arr.record_sonarr(db, {"eventType": "Download", "series": MOBLAND,
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
    movie = {"id": 3, "title": "Weapons", "year": 2025, "tmdbId": 1078605}
    records = [{"eventType": "downloadFolderImported", "movieId": 3, "movie": movie}]
    assert catch_up(db, "radarr", records) == 1
    assert catch_up(db, "radarr", records) == 0
    assert [r["title"] for r in db.pending_imports()] == ["Weapons (2025)"]
