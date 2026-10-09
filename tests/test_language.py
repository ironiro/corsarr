import asyncio

from corsarr import i18n
from corsarr.bot import CorsarrBot
from corsarr.db import now
from corsarr.llm import FeedbackIntent, SettingsChange, Understanding
from corsarr.models import Candidate


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """Replies with the language that was active when the bot asked for the text."""
    def __init__(self, language):
        self.language = language

    async def understand(self, text, recent):
        return Understanding(language=self.language, intent="chat", media_types=[], jellyfin_genres=[],
                             tmdb_movie_genre_ids=[], tmdb_tv_genre_ids=[], settings=SettingsChange(),
                             feedback=FeedbackIntent(rating="none", text=""))

    def requested_genres(self, und):
        return []

    async def say(self, situation, speaker, facts=None):
        return f"[{i18n.language()}]"


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)
        return type("M", (), {"message_id": 1})()

    async def send_chat_action(self, *args):
        pass


class FakeMessage:
    reply_to_message = None
    from_user = type("U", (), {"first_name": "Sam"})()

    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def make_bot(db, language):
    bot = CorsarrBot.__new__(CorsarrBot)
    bot.db, bot.llm, bot.down, bot.genres_loaded = db, FakeLLM(language), False, True
    bot.cfg = type("Cfg", (), {"chat_id": -100})()
    bot.app = type("App", (), {"bot": FakeTelegram()})()
    bot.speaker = lambda: "normal"
    return bot


def handle(bot, text):
    msg = FakeMessage()
    context = type("Ctx", (), {"bot": bot.app.bot})()

    async def go():
        with i18n.use_language(bot.chat_language()):
            await bot._handle(msg, text, context)
    run(go())
    return msg.replies


def test_answers_in_the_language_it_is_addressed_in(db):
    i18n.set_language("de")
    assert handle(make_bot(db, "en"), "hey, what can you do?") == ["[en]"]
    assert handle(make_bot(db, "de"), "na, was kannst du?") == ["[de]"]
    assert handle(make_bot(db, "fr"), "salut") == ["[en]"]  # no French texts: English instead


def test_unprompted_messages_use_the_groups_last_language(db):
    bot = make_bot(db, "en")
    handle(bot, "hi")  # the group wrote English last
    rid = db.add_request("season", "tv:1", "s1", "Andor (2022)", "tv", now(), status="pending",
                         extra={"season": 1, "series_done": False})
    assert run(bot.ask_feedback(db.request(rid)))
    assert bot.app.bot.sent[0].startswith("📺 <b>Andor (2022)</b> · season 1")


def test_logs_are_english_whatever_the_language():
    with i18n.use_language("de"):
        assert i18n.t("log.bot_stopped") == "Bot stopped"
        assert i18n.t("bot.btn_reject") == "🙅 Nicht interessiert"


def test_parallel_requests_keep_their_own_language():
    async def one(lang, seen):
        with i18n.use_language(lang):
            await asyncio.sleep(0.01)  # the other request runs in between
            seen.append((lang, i18n.t("bot.btn_reject")))

    async def both():
        seen = []
        await asyncio.gather(one("de", seen), one("en", seen))
        return dict(seen)

    assert run(both()) == {"de": "🙅 Nicht interessiert", "en": "🙅 Not interested"}


def test_a_card_stays_in_its_language(db):
    from test_carousel import make_bot as carousel_bot, FakeQuery
    bot = carousel_bot(db)
    lib = Candidate(media_type="movie", source="library", title="Heat", year=1995, jellyfin_id="jf", tmdb_id=9,
                    overview="Cops and robbers.")
    new = Candidate(media_type="movie", source="new", title="Ronin", year=1998, tmdb_id=8, overview="Heist.")
    with i18n.use_language("en"):
        run(bot._send_carousel([(lib, ""), (new, "")]))
    db.set_state("chat_language", "de")  # the group switched to German meanwhile
    q = FakeQuery()
    with i18n.use_language("de"):
        run(bot._cb_nav(q, 1, 1))
    media, markup = q.message.media_edits[0]
    assert "Not in your library" in media.caption and markup.inline_keyboard[0][0].text == "📥 Request"
