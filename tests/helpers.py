"""Fakes shared by the tests: Telegram objects, the services around the bot, a bot without Telegram, and the
web app with a fake runtime. Fixtures (db, env, …) live in conftest.py."""
import asyncio
from datetime import timedelta

import httpx
from aiohttp.test_utils import TestClient, TestServer

from corsarr import config, web
from corsarr.bot import CorsarrBot
from corsarr.db import DB, iso, now
from corsarr.feedback import FeedbackService
from corsarr.llm import Pick, Selection
from corsarr.profile import ProfileBuilder

TICKS_MIN = 600_000_000

# Environment of a complete configuration (see the `env` fixture in conftest.py).
ENV = {
    "TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-100", "ANTHROPIC_API_KEY": "sk-test",
    "JELLYFIN_URL": "http://jf:8096", "JELLYFIN_API_KEY": "jfkey", "JELLYFIN_USER": "Wohnzimmer",
    "JELLYSEERR_URL": "http://seerr:5055", "JELLYSEERR_API_KEY": "seerrkey", "WEBHOOK_SECRET": "hook",
    "ADMIN_PASSWORD": "pw",
}
H = {"X-Corsarr": "1"}  # the CSRF header every non-GET API call needs


def run(coro):
    return asyncio.run(coro)


class FakeConfig:
    """A Config with only the values given; everything else is ''."""
    def __init__(self, **values):
        self.values = values
        self.llm_provider = values.get("LLM_PROVIDER", "claude")
        self.chat_id = -100

    def get(self, name):
        return self.values.get(name, "")


# --- Telegram ------------------------------------------------------------------------------------

class Photo:
    def __init__(self, file_id):
        self.file_id = file_id


class Sent:
    """What Telegram returns for a sent/edited message; `photo` only for photo messages."""
    def __init__(self, message_id=42, file_id=None):
        self.message_id = message_id
        self.photo = [Photo(file_id)] if file_id else None


class FakeUser:
    def __init__(self, uid=1, name="Sam", language_code="en"):
        self.id, self.first_name, self.full_name, self.username = uid, name, name, name.lower()
        self.is_bot, self.language_code = False, language_code


class FakeMessage:
    """A message the bot may edit or reply to; every edit is recorded."""
    def __init__(self, text_html="q", reply_markup=None, photo=None, message_id=42):
        self.message_id, self.chat_id = message_id, -100
        self.text_html, self.caption_html, self.reply_markup, self.photo = text_html, "", reply_markup, photo
        self.reply_to_message, self.from_user = None, FakeUser()
        self.media_edits, self.caption_edits, self.edits, self.replies = [], [], [], []

    async def edit_media(self, media, reply_markup=None):
        self.media_edits.append((media, reply_markup))
        return Sent(self.message_id, f"file-{len(self.media_edits)}")

    async def edit_caption(self, caption=None, parse_mode=None, reply_markup=None):
        self.caption_edits.append((caption, reply_markup))

    async def edit_text(self, text, **kwargs):
        self.text_html, self.reply_markup = text, kwargs.get("reply_markup")
        self.edits.append((text, self.reply_markup))

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeQuery:
    """A pressed button: `data` is the callback data, `answers` what the bot answered to the tap."""
    def __init__(self, data=None, user=None, message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.from_user = user or FakeUser()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


class FakeTelegram:
    """The Bot API: records sent photos (photo, caption, markup) and messages (text, kwargs)."""
    def __init__(self, message_id=42, file_id="file-page-1"):
        self.message_id, self.file_id = message_id, file_id
        self.photos, self.messages, self.recipients = [], [], []

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        self.photos.append((photo, caption, reply_markup))
        return Sent(self.message_id, self.file_id)

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((text, kwargs))
        self.recipients.append(chat_id)
        return Sent(self.message_id)

    async def send_chat_action(self, *args):
        pass


# --- the services around the bot ------------------------------------------------------------------

class FakeJellyfin:
    def __init__(self, library=(), items=None, season=None):
        self.library = list(library)
        self._items = items or {}
        self.season = season or []

    async def unwatched_candidates(self, genres, media_types):
        return [c for c in self.library if c.media_type in media_types]

    async def taste_items(self):
        return [{"Genres": ["Thriller"]}, {"Genres": ["Horror"], "_favorite": True}]

    async def item(self, item_id):
        return self._items.get(item_id)

    async def season_episodes(self, series_id, season_id):
        return self.season

    async def series_finished(self, series_id):
        return False

    async def poster(self, item_id):
        return b"jpeg-bytes"


class FakeSeerr:
    def __init__(self, new=()):
        self.new = list(new)

    async def discover(self, media_type, genre_ids, exclude, want=15, max_pages=3):
        return [c for c in self.new if c.media_type == media_type and c.key not in exclude]

    async def enrich(self, cands):
        pass

    async def keywords_for(self, media_type, tmdb_id):
        return ["Horror"], ["gore", "slasher"]


class RequestSeerr:
    """Only requests; `fail` maps tmdb ids to the HTTP status their request fails with."""
    def __init__(self, fail=()):
        self.fail, self.requested = dict(fail), []

    async def request(self, media_type, tmdb_id):
        self.requested.append((media_type, tmdb_id))
        if tmdb_id in self.fail:
            req = httpx.Request("POST", "http://seerr/api/v1/request")
            raise httpx.HTTPStatusError("x", request=req, response=httpx.Response(self.fail[tmdb_id], request=req))
        return {"id": tmdb_id}


class FakeLLM:
    """Picks the given candidate ids; remembers what it was shown."""
    def __init__(self, pick_ids):
        self.pick_ids = pick_ids
        self.seen = None
        self.taste = None

    def requested_genres(self, und):
        return list(und.jellyfin_genres)

    async def select(self, request, cands, speaker, n_min, n_max, notes, genres=None, taste=None):
        self.seen = (cands, n_min, n_max, notes)
        self.taste = taste
        return Selection(intro="📱 hi", picks=[Pick(id=i, reason="📱 passt") for i in self.pick_ids])


class SayLLM:
    """Only says things; `said` holds the facts of every call."""
    def __init__(self):
        self.said = []

    async def say(self, situation, speaker, facts=None):
        self.said.append(facts)
        return "📱 on its way"


class NoTags(FeedbackService):
    async def title_tags(self, req):
        return ["Horror"], ["gore"]


class Profiles:
    def invalidate(self):
        pass


# --- the bot without Telegram ------------------------------------------------------------------

def make_bot(db, seerr=None, llm=None, feedback=None):
    """A CorsarrBot without __init__: fake Telegram, Jellyfin posters, requests and character lines."""
    bot = CorsarrBot.__new__(CorsarrBot)
    bot.db, bot.jellyfin, bot.seerr, bot.llm = db, FakeJellyfin(), seerr or RequestSeerr(), llm or SayLLM()
    bot.feedback = feedback
    bot.cfg = FakeConfig()
    bot.app = type("App", (), {"bot": FakeTelegram()})()
    bot.speaker = lambda: "normal"
    bot.down, bot.genres_loaded = False, True
    bot._card_locks, bot._nav_wanted, bot._probing = {}, {}, False  # set up by __init__, which this skips
    return bot


def movie_item(played=False, pos_min=0, runtime_min=100):
    return {"Id": "m1", "Type": "Movie", "Name": "Film", "ProductionYear": 2021,
            "ProviderIds": {"Tmdb": "555"}, "RunTimeTicks": runtime_min * TICKS_MIN,
            "UserData": {"Played": played, "PlaybackPositionTicks": pos_min * TICKS_MIN}}


def make_feedback(db, items, season=None):
    """A FeedbackService whose notifier marks questions as sent; returns (service, questions sent)."""
    jf = FakeJellyfin(items=items, season=season)
    svc = FeedbackService(db, jf, FakeSeerr(), ProfileBuilder(db, jf))
    sent = []

    async def notifier(req):
        sent.append(req)
        db.update_request(req["id"], status="sent", sent_at=now(), message_id=len(sent))
        return True

    svc.notifier = notifier
    return svc, sent


def labels(markup):
    """Button texts of an inline keyboard, row by row."""
    return [[b.text for b in row] for row in markup.inline_keyboard] if markup else []


# --- Sonarr / Radarr -------------------------------------------------------------------------------

def episodes(seasons, per_season, aired):
    return [{"seasonNumber": s, "episodeNumber": e, "title": f"Folge {e}", "airDateUtc": aired}
            for s in seasons for e in range(1, per_season + 1)]


def sonarr(eps, upgrade=False, series_id=7, title="Grey's Anatomy", year=2005, tmdb=1416):
    return {"eventType": "Download", "isUpgrade": upgrade, "episodes": eps,
            "series": {"id": series_id, "title": title, "year": year, "tmdbId": tmdb}}


def age_imports(db, minutes):
    db.conn.execute("UPDATE arr_imports SET created_at=?", (iso(now() - timedelta(minutes=minutes)),))
    db.conn.commit()


# --- the web app ----------------------------------------------------------------------------------

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

    def apply_live_config(self):  # like Runtime.apply_live_config
        self.cfg = config.load()
        if seerr := getattr(self, "seerr", None):
            seerr.set_streaming(self.cfg.streaming_region, self.cfg.streaming_ids)

    async def check(self, manual=False):
        self.checks += 1

    async def restore(self, settings, database):
        from corsarr import backup
        self.db.conn.close()
        await asyncio.to_thread(backup.restore, self.cfg.data_dir, settings, database)
        self.cfg = config.load()
        self.db = DB(self.cfg.db_path)


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
