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
