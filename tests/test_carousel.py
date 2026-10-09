import asyncio

from corsarr.bot import CorsarrBot
from corsarr.models import Candidate


class Photo:
    def __init__(self, file_id):
        self.file_id = file_id


class Sent:
    """What Telegram returns for a sent/edited photo message."""
    def __init__(self, message_id, file_id):
        self.message_id = message_id
        self.photo = [Photo(file_id)]


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        self.sent.append((photo, caption, reply_markup))
        return Sent(42, "file-page-1")


class FakeMessage:
    def __init__(self):
        self.media_edits = []
        self.caption_edits = []

    async def edit_media(self, media, reply_markup=None):
        self.media_edits.append((media, reply_markup))
        return Sent(42, f"file-{len(self.media_edits)}")

    async def edit_caption(self, caption=None, parse_mode=None, reply_markup=None):
        self.caption_edits.append((caption, reply_markup))


class FakeQuery:
    def __init__(self):
        self.message = FakeMessage()
        self.from_user = type("U", (), {"first_name": "Sam"})()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


class FakeJellyfin:
    async def poster(self, item_id):
        return b"jpeg-bytes"


def make_bot(db):
    bot = CorsarrBot.__new__(CorsarrBot)
    bot.db, bot.jellyfin = db, FakeJellyfin()
    bot.cfg = type("Cfg", (), {"chat_id": -100})()
    bot.app = type("App", (), {"bot": FakeTelegram()})()
    return bot


def picks():
    lib = Candidate(media_type="movie", source="library", title="John Wick", year=2014, jellyfin_id="jf1",
                    tmdb_id=1, overview="Kung Fu.", trailer_url="https://www.youtube.com/watch?v=a")
    new1 = Candidate(media_type="movie", source="new", title="Runner", year=2026, tmdb_id=2, overview="Kurier.",
                     poster_url="https://image.tmdb.org/t/p/w500/r.jpg")
    new2 = Candidate(media_type="tv", source="new", title="Reacher", year=2022, tmdb_id=3, overview="Ex-MP.")
    return [(lib, "📱 passt, weil kung fu"), (new1, "📱 passt, weil kurier"), (new2, "")]


def labels(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_one_message_for_all_suggestions(db):
    bot = make_bot(db)
    asyncio.run(bot._send_carousel(picks()))
    [(photo, caption, markup)] = bot.app.bot.sent  # a single message, not one per title
    assert photo == b"jpeg-bytes" and "John Wick (2014)" in caption and "kung fu" in caption
    assert labels(markup) == [["✅ Schauen wir", "🙅 Nicht interessiert"], ["◀️", "1 / 3", "▶️"], ["🎬 Trailer"]]
    car = db.carousel(1)
    assert car["message_id"] == 42 and db.carousel_pages(1)[0]["file_id"] == "file-page-1"


def test_paging_shows_the_other_titles(db):
    bot = make_bot(db)
    asyncio.run(bot._send_carousel(picks()))
    q = FakeQuery()
    asyncio.run(bot._cb_nav(q, 1, 1))
    media, markup = q.message.media_edits[0]
    assert media.media == "https://image.tmdb.org/t/p/w500/r.jpg" and "Runner (2026)" in media.caption
    assert labels(markup) == [["📥 Anfragen", "🙅 Nicht interessiert"], ["◀️", "2 / 3", "▶️"]]
    assert db.carousel(1)["position"] == 1
    asyncio.run(bot._cb_nav(q, 1, 2))  # no poster -> stand-in image instead of failing
    media, _ = q.message.media_edits[1]
    assert "Reacher (2022)" in media.caption and media.media.input_file_content.startswith(b"\x89PNG")


def test_decision_redraws_only_that_page(db):
    bot = make_bot(db)
    asyncio.run(bot._send_carousel(picks()))
    q = FakeQuery()
    sid = db.carousel_pages(1)[0]["id"]
    asyncio.run(bot._cb_accept(q, sid))
    caption, markup = q.message.caption_edits[0]
    assert "Ausgewählt von Sam" in caption
    assert labels(markup) == [["◀️", "1 / 3", "▶️"], ["🎬 Trailer"]]  # actions gone, paging + trailer stay
    q2 = FakeQuery()
    asyncio.run(bot._cb_nav(q2, 1, 0))  # paging back later still shows the decision
    media, markup = q2.message.media_edits[0]
    assert "Ausgewählt von Sam" in media.caption and labels(markup)[0] == ["◀️", "1 / 3", "▶️"]
    assert media.media == "file-page-1"  # poster reused, not downloaded again
