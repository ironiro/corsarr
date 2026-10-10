"""Retention: the daily job drops what nothing reads any more – and nothing more than that."""
import asyncio
import json
from datetime import timedelta

from corsarr import arr, backup, i18n, retention
from corsarr.db import iso, now
from corsarr.models import Candidate
from helpers import make_bot, sonarr


def days_ago(n):
    return iso(now() - timedelta(days=n))


def age(db, table, days, where="1=1", params=()):
    db.conn.execute(f"UPDATE {table} SET {'ts' if table == 'llm_usage' else 'created_at'}=? WHERE {where}",
                    (days_ago(days), *params))
    db.conn.commit()


def count(db, table, where="1=1"):
    return db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def test_usage_older_than_thirteen_months_goes(db):
    for i in range(3):
        db.add_usage("claude", "claude-haiku-5-5", "text", {"input": 100})
    db.conn.execute("UPDATE llm_usage SET ts=? WHERE id=1", (days_ago(14 * 31),))
    db.conn.execute("UPDATE llm_usage SET ts=? WHERE id=2", (days_ago(12 * 31),))
    db.conn.commit()
    assert db.prune_usage(now() - timedelta(days=retention.LLM_USAGE_MONTHS * 31)) == 1
    assert [r["id"] for r in db.conn.execute("SELECT id FROM llm_usage ORDER BY id")] == [2, 3]


def test_only_notified_old_imports_go(db):
    aired = "2013-01-01T01:00:00Z"
    for sid in (1, 2, 3):
        arr.record_sonarr(db, sonarr([{"seasonNumber": 1, "episodeNumber": 1, "airDateUtc": aired}], series_id=sid))
    age(db, "arr_imports", 40, "group_key IN ('sonarr:1', 'sonarr:2')")  # old …
    db.mark_imports_notified([1])  # … one of them sent, one still pending
    db.mark_imports_notified([3])  # sent, but recent
    assert db.prune_imports(now() - timedelta(days=retention.IMPORTS_DAYS)) == 1
    assert [r["group_key"] for r in db.conn.execute("SELECT group_key FROM arr_imports ORDER BY id")] == \
        ["sonarr:2", "sonarr:3"]
    assert retention.IMPORTS_DAYS * 86400 >= arr.KNOWN_FOR.total_seconds()  # catch-up's dedup window stays intact


def cards(bot, titles, days):
    """A carousel with one suggestion per title, `days` old; returns the suggestion ids."""
    picks = [(Candidate(media_type="movie", source="new", title=t, year=2000, tmdb_id=i, overview="x"), "")
             for i, t in enumerate(titles, start=100 * days + 1)]
    asyncio.run(bot._send_carousel(picks))
    cid = bot.db.conn.execute("SELECT MAX(id) FROM carousels").fetchone()[0]
    age(bot.db, "carousels", days, "id=?", (cid,))
    age(bot.db, "suggestions", days, "carousel_id=?", (cid,))
    return [r["id"] for r in bot.db.carousel_pages(cid)]


def test_old_cards_go_but_requested_titles_stay_for_the_download_note(db):
    bot = make_bot(db)
    old = cards(bot, ["Old A", "Old B"], 100)
    recent = cards(bot, ["New A"], 10)
    db.set_suggestion_status(old[0], "requested", "Sam")  # Radarr's download message says "via Corsarr"
    db.set_suggestion_status(recent[0], "accepted", "Sam")
    removed = db.prune_cards(now() - timedelta(days=retention.CARDS_DAYS))
    assert removed == {"carousels": 1, "collections": 0, "suggestions": 1}
    assert db.suggestion(old[1]) is None and db.carousel(1) is None
    kept = db.suggestion(old[0])
    assert kept["status"] == "requested" and kept["card"] is None and kept["file_id"] is None
    assert db.was_requested("movie", kept["tmdb_id"])
    assert db.suggestion(recent[0])["card"] is not None and db.carousel(2) is not None
    assert db.accepted_keys() == {"movie:1001"}  # the 14-day window the recommender uses still works


def test_old_collections_go(db):
    db.add_collection(119, json.dumps({"name": "Saga", "parts": []}))
    db.add_collection(120, json.dumps({"name": "Fresh", "parts": []}))
    age(db, "collections", 100, "id=1")
    assert db.prune_cards(now() - timedelta(days=retention.CARDS_DAYS))["collections"] == 1
    assert db.collection(1) is None and db.collection(2) is not None


def test_translations_of_removed_texts_go(db):
    db.save_translations("fr", {"bot.btn_accept": ("Accept", "Accepter"), "bot.gone_in_an_update": ("x", "y")})
    assert db.prune_translations(set(i18n.translatable())) == 1
    assert [key for _, key, _, _ in db.translations()] == ["bot.btn_accept"]
    assert db.prune_translations(set(i18n.translatable())) == 0


def test_only_the_newest_pre_restore_copies_are_kept(tmp_path):
    folder = tmp_path / backup.RESTORE_DIR
    for stamp in ("20260101-000000", "20260301-000000", "20260201-000000", "20260401-000000"):
        (folder / f"{backup.RESTORE_PREFIX}{stamp}").mkdir(parents=True)
        (folder / f"{backup.RESTORE_PREFIX}{stamp}" / "corsarr.db").write_bytes(b"x")
    (folder / "something-else").mkdir()
    assert backup.prune_restores(tmp_path, keep=3) == 1
    assert sorted(p.name for p in folder.iterdir()) == [
        "pre-restore-20260201-000000", "pre-restore-20260301-000000", "pre-restore-20260401-000000", "something-else"]
    assert backup.prune_restores(tmp_path, keep=3) == 0
    assert backup.prune_restores(tmp_path / "missing", keep=3) == 0


def test_the_daily_job_runs_every_rule_and_logs_a_summary(db, tmp_path, caplog):
    bot = make_bot(db)
    bot.cfg.data_dir = tmp_path
    db.add_usage("claude", "claude-haiku-5-5", "text", {"input": 100})
    age(db, "llm_usage", 500)
    cards(bot, ["Old"], 100)
    with caplog.at_level("INFO", logger="corsarr.jobs"):
        asyncio.run(bot.job_retention(None))
    assert count(db, "llm_usage") == 0 and count(db, "suggestions") == 0 and count(db, "carousels") == 0
    assert "usage 1" in caplog.text and "suggestions 1" in caplog.text and "carousels 1" in caplog.text
    caplog.clear()
    with caplog.at_level("INFO", logger="corsarr.jobs"):
        asyncio.run(bot.job_retention(None))  # nothing to do: no log line
    assert "Retention" not in caplog.text


def test_indexes_exist_also_on_a_database_from_an_older_version(db):
    names = {r["name"] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ix_suggestions_carousel", "ix_suggestions_requested", "ix_fr_message", "ix_arr_episode"} <= names
