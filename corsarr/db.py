"""SQLite memory: suggestions, rejections, feedback, traits, settings, watch state."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import persona

DEFAULT_SETTINGS: dict[str, Any] = {
    "series_pause_days": 14,
    "abort_days": 3,
    **persona.default_settings(),  # <character>_enabled for every character (persona.py)
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_key TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    source TEXT NOT NULL,
    tmdb_id INTEGER,
    jellyfin_id TEXT,
    status TEXT NOT NULL DEFAULT 'suggested',
    message_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_suggestions_key ON suggestions(title_key);
CREATE TABLE IF NOT EXISTS state (           -- small internal values, e.g. the group's language
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS carousels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collections (    -- one message per film series ("all parts of …")
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER NOT NULL,          -- TMDB collection id
    card TEXT NOT NULL,                -- JSON: name, poster, intro, language, parts (missing ones with suggestion id)
    message_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rejected (
    title_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title_key TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    rating INTEGER NOT NULL,          -- -1, 0, 1
    weight REAL NOT NULL DEFAULT 1.0, -- 0.3 for an unanswered abort question
    free_text TEXT,
    genres TEXT NOT NULL DEFAULT '[]',
    keywords TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    direction TEXT NOT NULL,           -- 'more' | 'less'
    trait TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    weight REAL NOT NULL DEFAULT 1.0,
    feedback_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- 'movie' | 'season' | 'series_pause' | 'abort'
    title_key TEXT NOT NULL,
    jellyfin_id TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    extra TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,              -- 'scheduled' | 'pending' | 'sending' | 'sent' | 'answered' | 'expired' | 'cancelled'
    due_at TEXT NOT NULL,
    sent_at TEXT,
    message_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fr_status ON feedback_requests(status, due_at);
CREATE TABLE IF NOT EXISTS arr_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                -- 'episode' | 'movie'
    group_key TEXT NOT NULL,           -- 'sonarr:<series id>' | 'radarr:<movie id>'
    title TEXT NOT NULL,
    tmdb_id INTEGER,
    season INTEGER,
    episode INTEGER,
    episode_title TEXT,
    fresh INTEGER NOT NULL DEFAULT 0,  -- aired recently (new episode) vs. backfill
    created_at TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_arr_pending ON arr_imports(notified, group_key);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,                  -- UTC
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,                -- what the call was for: Understanding, Selection, text, …
    input INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    cache_write INTEGER NOT NULL DEFAULT 0,
    output INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_llm_usage_ts ON llm_usage(ts);
CREATE TABLE IF NOT EXISTS translations (
    lang TEXT NOT NULL,                -- language without built-in texts, e.g. 'fr'
    key TEXT NOT NULL,                 -- i18n key, e.g. 'bot.btn_accept'
    source TEXT NOT NULL,              -- the English text it was translated from (changes -> translate again)
    text TEXT NOT NULL,
    PRIMARY KEY (lang, key)
);
CREATE TABLE IF NOT EXISTS watch_state (
    jellyfin_id TEXT PRIMARY KEY,      -- movie id or series id
    title_key TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL,
    last_activity TEXT NOT NULL,
    last_pct REAL NOT NULL DEFAULT 0,
    finished INTEGER NOT NULL DEFAULT 0
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


# Database format version (SQLite user_version). Raise it whenever _migrate changes something, so an
# older Corsarr – e.g. after switching from beta back to stable – refuses a database it doesn't understand.
SCHEMA_VERSION = 2  # 2: who gave a rating (feedback/traits rater_id, rater_name)


class DatabaseTooNew(Exception):
    """The database was written by a newer Corsarr version."""

    def __init__(self, found: int):
        super().__init__(f"database version {found}, this Corsarr understands up to {SCHEMA_VERSION}")
        self.found = found


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        found = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if found > SCHEMA_VERSION:
            self.conn.close()
            raise DatabaseTooNew(found)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns that newer versions need to tables created by older ones."""
        added = {
            "suggestions": {
                "carousel_id": "INTEGER",   # the browsable card this suggestion is a page of
                "card": "TEXT",            # JSON snapshot of the candidate + reason, to redraw the page
                "decided_by": "TEXT",      # first name of whoever pressed accept/reject/request
                "file_id": "TEXT",         # Telegram file id of the poster once uploaded
            },
            # Who rated – NULL for ratings from before per-person taste (they count for everyone).
            "feedback": {"rater_id": "INTEGER", "rater_name": "TEXT"},
            "traits": {"rater_id": "INTEGER", "rater_name": "TEXT"},
        }
        for table, columns in added.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in columns.items():
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    # --- settings -------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        result = dict(DEFAULT_SETTINGS)
        for row in self.conn.execute("SELECT key, value FROM settings"):
            result[row["key"]] = json.loads(row["value"])
        return result

    def set_setting(self, key: str, value: Any) -> None:
        if key not in DEFAULT_SETTINGS:
            raise KeyError(key)
        self._exec(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self._exec("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))

    # --- suggestions ----------------------------------------------------
    def add_suggestion(self, c) -> int:
        cur = self._exec(
            "INSERT INTO suggestions(title_key, title, media_type, source, tmdb_id, jellyfin_id, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (c.key, c.label, c.media_type, c.source, c.tmdb_id, c.jellyfin_id, iso(now())),
        )
        return int(cur.lastrowid)

    def suggestion(self, suggestion_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM suggestions WHERE id=?", (suggestion_id,)).fetchone()

    def set_suggestion_status(self, suggestion_id: int, status: str, decided_by: str | None = None) -> None:
        self._exec("UPDATE suggestions SET status=?, decided_by=? WHERE id=?",
                   (status, decided_by, suggestion_id))

    # --- carousels (one browsable message per recommendation) ------------
    def create_carousel(self, pages: list[tuple[int, str]]) -> int:
        """pages = [(suggestion id, card JSON)] in display order."""
        cid = int(self._exec("INSERT INTO carousels(created_at) VALUES(?)", (iso(now()),)).lastrowid)
        for sid, card in pages:
            self._exec("UPDATE suggestions SET carousel_id=?, card=? WHERE id=?", (cid, card, sid))
        return cid

    def carousel(self, carousel_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM carousels WHERE id=?", (carousel_id,)).fetchone()

    def carousel_pages(self, carousel_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM suggestions WHERE carousel_id=? ORDER BY id", (carousel_id,)
        ).fetchall()

    def set_carousel(self, carousel_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE carousels SET {cols} WHERE id=?", (*fields.values(), carousel_id))

    def set_suggestion_file_id(self, suggestion_id: int, file_id: str) -> None:
        self._exec("UPDATE suggestions SET file_id=? WHERE id=?", (file_id, suggestion_id))

    # --- film series (collections) ------------------------------------------
    def add_collection(self, tmdb_id: int, card: str) -> int:
        cur = self._exec("INSERT INTO collections(tmdb_id, card, created_at) VALUES(?,?,?)",
                         (int(tmdb_id), card, iso(now())))
        return int(cur.lastrowid)

    def collection(self, collection_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM collections WHERE id=?", (collection_id,)).fetchone()

    def set_collection_message(self, collection_id: int, message_id: int) -> None:
        self._exec("UPDATE collections SET message_id=? WHERE id=?", (message_id, collection_id))

    def recently_suggested(self, days: int = 3) -> set[str]:
        since = iso(now() - timedelta(days=days))
        rows = self.conn.execute(
            "SELECT DISTINCT title_key FROM suggestions WHERE created_at >= ?", (since,)
        )
        return {r["title_key"] for r in rows}

    # --- rejections -----------------------------------------------------
    def reject(self, key: str, title: str) -> None:
        self._exec(
            "INSERT OR IGNORE INTO rejected(title_key, title, created_at) VALUES(?,?,?)",
            (key, title, iso(now())),
        )

    def accepted_keys(self, days: int = 14) -> set[str]:
        """Titles someone picked recently – they are about to watch them, so don't suggest them again."""
        since = iso(now() - timedelta(days=days))
        rows = self.conn.execute(
            "SELECT DISTINCT title_key FROM suggestions WHERE status='accepted' AND created_at >= ?", (since,)
        )
        return {r["title_key"] for r in rows}

    def rejected_keys(self) -> set[str]:
        return {r["title_key"] for r in self.conn.execute("SELECT title_key FROM rejected")}

    # --- feedback & traits ---------------------------------------------
    def add_feedback(
        self,
        key: str,
        title: str,
        media_type: str,
        rating: int,
        weight: float = 1.0,
        free_text: str | None = None,
        genres: list[str] | None = None,
        keywords: list[str] | None = None,
        rater: tuple[int, str] | None = None,
    ) -> int:
        rater_id, rater_name = rater or (None, None)
        cur = self._exec(
            "INSERT INTO feedback(title_key, title, media_type, rating, weight, free_text, genres, "
            "keywords, created_at, rater_id, rater_name) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (key, title, media_type, rating, weight, free_text,
             json.dumps(genres or []), json.dumps(keywords or []), iso(now()), rater_id, rater_name),
        )
        return int(cur.lastrowid)

    def delete_feedback(self, feedback_id: int) -> None:
        """A rating that was changed: the new one replaces it."""
        self._exec("DELETE FROM feedback WHERE id=?", (feedback_id,))

    def all_feedback(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM feedback ORDER BY id")
        return [
            {**dict(r), "genres": json.loads(r["genres"]), "keywords": json.loads(r["keywords"])}
            for r in rows
        ]

    def feedback_keys(self, min_weight: float = 0.0) -> set[str]:
        """Titles with a rating – with min_weight=1 only real ones, not the light automatic thumbs down."""
        return {r["title_key"] for r in self.conn.execute(
            "SELECT title_key FROM feedback WHERE weight >= ?", (min_weight,))}

    def feedback_for(self, key: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM feedback WHERE title_key=? ORDER BY id", (key,))]

    def add_trait(self, direction: str, trait: str, keywords: list[str], feedback_id: int | None,
                  weight: float = 1.0, rater: tuple[int, str] | None = None) -> None:
        rater_id, rater_name = rater or (None, None)
        self._exec(
            "INSERT INTO traits(direction, trait, keywords, weight, feedback_id, created_at, rater_id, rater_name) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (direction, trait, json.dumps([k.lower() for k in keywords]), weight, feedback_id,
             iso(now()), rater_id, rater_name),
        )

    def traits(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM traits ORDER BY id")
        return [{**dict(r), "keywords": json.loads(r["keywords"])} for r in rows]

    # --- feedback requests ----------------------------------------------
    def add_request(self, kind: str, key: str, jellyfin_id: str, title: str, media_type: str,
                    due_at: datetime, status: str = "scheduled", extra: dict | None = None) -> int:
        cur = self._exec(
            "INSERT INTO feedback_requests(kind, title_key, jellyfin_id, title, media_type, extra, "
            "status, due_at, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, key, jellyfin_id, title, media_type, json.dumps(extra or {}), status,
             iso(due_at), iso(now())),
        )
        return int(cur.lastrowid)

    def request(self, request_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM feedback_requests WHERE id=?", (request_id,)).fetchone()
        return self._req_dict(row) if row else None

    def request_by_message(self, message_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM feedback_requests WHERE message_id=?", (message_id,)
        ).fetchone()
        return self._req_dict(row) if row else None

    def open_requests_for(self, jellyfin_id: str, kinds: tuple[str, ...]) -> list[dict]:
        marks = ",".join("?" * len(kinds))
        rows = self.conn.execute(
            f"SELECT * FROM feedback_requests WHERE jellyfin_id=? AND kind IN ({marks}) "
            "AND status IN ('scheduled','pending','sending','sent')",
            (jellyfin_id, *kinds),
        )
        return [self._req_dict(r) for r in rows]

    def due_requests(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM feedback_requests WHERE status IN ('scheduled','pending') AND due_at <= ? "
            "ORDER BY due_at",
            (iso(now()),),
        )
        return [self._req_dict(r) for r in rows]

    def sent_requests(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM feedback_requests WHERE status='sent'")
        return [self._req_dict(r) for r in rows]

    def claim_request(self, request_id: int) -> bool:
        """Mark a pending request as being sent – atomically, so the job and the webhook never post the same
        question twice. False when it is not pending (any more). sent_at holds the claim time until the
        question is posted (then its real send time) or the claim is given back."""
        cur = self._exec("UPDATE feedback_requests SET status='sending', sent_at=? WHERE id=? AND status='pending'",
                         (iso(now()), request_id))
        return cur.rowcount == 1

    def stuck_sending(self, older_than: datetime) -> list[dict]:
        """Claims nobody gave back (e.g. a crash while posting) – to be put back to pending."""
        rows = self.conn.execute("SELECT * FROM feedback_requests WHERE status='sending' AND sent_at < ?",
                                 (iso(older_than),))
        return [self._req_dict(r) for r in rows]

    def recent_requests(self, days: int = 7) -> list[dict]:
        since = iso(now() - timedelta(days=days))
        rows = self.conn.execute(
            "SELECT * FROM feedback_requests WHERE created_at >= ? AND status IN ('sent','answered') "
            "ORDER BY id DESC LIMIT 10",
            (since,),
        )
        return [self._req_dict(r) for r in rows]

    def pause_asked(self, jellyfin_id: str, activity: str) -> bool:
        """Whether a pause question already exists for this period of inactivity."""
        rows = self.conn.execute(
            "SELECT extra FROM feedback_requests WHERE kind='series_pause' AND jellyfin_id=?",
            (jellyfin_id,),
        )
        return any(json.loads(r["extra"]).get("activity") == activity for r in rows)

    def update_request(self, request_id: int, **fields: Any) -> None:
        if "extra" in fields:
            fields["extra"] = json.dumps(fields["extra"])
        for k in ("due_at", "sent_at"):
            if isinstance(fields.get(k), datetime):
                fields[k] = iso(fields[k])
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE feedback_requests SET {cols} WHERE id=?", (*fields.values(), request_id))

    @staticmethod
    def _req_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["extra"] = json.loads(d["extra"])
        return d

    # --- sonarr / radarr imports -----------------------------------------
    def add_import(self, kind: str, group_key: str, title: str, tmdb_id: int | None, season: int | None,
                   episode: int | None, episode_title: str | None, fresh: bool) -> None:
        self._exec(
            "INSERT INTO arr_imports(kind, group_key, title, tmdb_id, season, episode, episode_title, fresh, "
            "created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, group_key, title, tmdb_id, season, episode, episode_title, int(fresh), iso(now())),
        )

    def has_import(self, group_key: str, season: int | None, episode: int | None, since: datetime) -> bool:
        """Whether this episode (or movie: season/episode None) was recorded since `since`."""
        row = self.conn.execute(
            "SELECT 1 FROM arr_imports WHERE group_key=? AND season IS ? AND episode IS ? AND created_at>=? LIMIT 1",
            (group_key, season, episode, iso(since))).fetchone()
        return row is not None

    # --- language model usage --------------------------------------------------
    def add_usage(self, provider: str, model: str, kind: str, usage: dict) -> None:
        self._exec("INSERT INTO llm_usage(ts, provider, model, kind, input, cache_read, cache_write, output) "
                   "VALUES(?,?,?,?,?,?,?,?)",
                   (iso(now()), provider, model, kind, usage.get("input", 0), usage.get("cache_read", 0),
                    usage.get("cache_write", 0), usage.get("output", 0)))

    def usage_since(self, since: datetime | None = None) -> list[dict]:
        """Token sums per provider, model and kind since `since` (everything when None)."""
        rows = self.conn.execute(
            "SELECT provider, model, kind, COUNT(*) AS calls, SUM(input) AS input, SUM(cache_read) AS cache_read, "
            "SUM(cache_write) AS cache_write, SUM(output) AS output FROM llm_usage WHERE ts >= ? "
            "GROUP BY provider, model, kind", (iso(since) if since else "",))
        return [dict(r) for r in rows]

    # --- translations of fixed texts ----------------------------------------
    def translations(self) -> list[tuple[str, str, str, str]]:
        return [tuple(r) for r in self.conn.execute("SELECT lang, key, source, text FROM translations")]

    def save_translations(self, lang: str, texts: dict[str, tuple[str, str]]) -> None:
        """texts: key -> (English source, translation)"""
        self.conn.executemany(
            "INSERT INTO translations(lang, key, source, text) VALUES(?,?,?,?) "
            "ON CONFLICT(lang, key) DO UPDATE SET source=excluded.source, text=excluded.text",
            [(lang, k, src, txt) for k, (src, txt) in texts.items()])
        self.conn.commit()

    def pending_imports(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM arr_imports WHERE notified=0 ORDER BY id")
        return [dict(r) for r in rows]

    def mark_imports_notified(self, ids: list[int]) -> None:
        marks = ",".join("?" * len(ids))
        self._exec(f"UPDATE arr_imports SET notified=1 WHERE id IN ({marks})", tuple(ids))

    def was_requested(self, media_type: str, tmdb_id: int | None) -> bool:
        """Whether this title was requested through the bot's 📥 button."""
        if not tmdb_id:
            return False
        row = self.conn.execute(
            "SELECT 1 FROM suggestions WHERE media_type=? AND tmdb_id=? AND status='requested' LIMIT 1",
            (media_type, int(tmdb_id)),
        ).fetchone()
        return row is not None

    # --- watch state ----------------------------------------------------
    def watch_state(self, jellyfin_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM watch_state WHERE jellyfin_id=?", (jellyfin_id,)).fetchone()
        return dict(row) if row else None

    def touch_watch_state(self, jellyfin_id: str, key: str, title: str, media_type: str,
                          pct: float, finished: bool) -> None:
        self._exec(
            "INSERT INTO watch_state(jellyfin_id, title_key, title, media_type, last_activity, last_pct, "
            "finished) VALUES(?,?,?,?,?,?,?) ON CONFLICT(jellyfin_id) DO UPDATE SET "
            "last_activity=excluded.last_activity, last_pct=excluded.last_pct, "
            "finished=excluded.finished, title=excluded.title",
            (jellyfin_id, key, title, media_type, iso(now()), pct, int(finished)),
        )

    def series_states(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM watch_state WHERE media_type='tv' AND finished=0"
        )
        return [dict(r) for r in rows]
