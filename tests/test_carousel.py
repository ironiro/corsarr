"""The browsable card: one photo message per recommendation, ◀️ ▶️ page through the suggestions."""
import asyncio

from telegram.error import TelegramError

from corsarr.models import Candidate
from helpers import FakeQuery, Sent, labels, make_bot


def picks():
    lib = Candidate(media_type="movie", source="library", title="John Wick", year=2014, jellyfin_id="jf1",
                    tmdb_id=1, overview="Kung Fu.", trailer_url="https://www.youtube.com/watch?v=a")
    new1 = Candidate(media_type="movie", source="new", title="Runner", year=2026, tmdb_id=2, overview="Kurier.",
                     poster_url="https://image.tmdb.org/t/p/w500/r.jpg")
    new2 = Candidate(media_type="tv", source="new", title="Reacher", year=2022, tmdb_id=3, overview="Ex-MP.")
    return [(lib, "📱 passt, weil kung fu"), (new1, "📱 passt, weil kurier"), (new2, "")]


def test_one_message_for_all_suggestions(db):
    bot = make_bot(db)
    asyncio.run(bot._send_carousel(picks()))
    [(photo, caption, markup)] = bot.app.bot.photos  # a single message, not one per title
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


def test_fast_taps_end_on_the_last_wished_page_without_overlapping_edits(db):
    """Telegram cancels an edit when the next one for the same message arrives first – so edits of one
    card must never overlap, and a burst of taps only needs the newest page."""
    bot = make_bot(db)
    asyncio.run(bot._send_carousel(picks()))
    cid = db.conn.execute("SELECT id FROM carousels").fetchone()[0]
    q = FakeQuery()
    active, overlaps = [0], [0]

    async def slow_edit(media, reply_markup=None):
        active[0] += 1
        overlaps[0] = max(overlaps[0], active[0])
        await asyncio.sleep(0.02)  # Telegram fetching the poster
        active[0] -= 1
        q.message.media_edits.append((media, reply_markup))
        return Sent(42, f"file-{len(q.message.media_edits)}")
    q.message.edit_media = slow_edit

    async def taps():
        await asyncio.gather(*(bot._cb_nav(q, cid, p) for p in (1, 0, 1)))
    asyncio.run(taps())
    assert overlaps[0] == 1 and len(q.answers) == 3  # every tap answered, edits one after another
    assert db.carousel(cid)["position"] == 1 and len(q.message.media_edits) <= 2


def test_a_tap_during_a_decision_is_shown_afterwards(db):
    """Accepting holds the card's lock while the caption is edited – a page tap arriving meanwhile must
    not be dropped: whoever holds the lock shows the wished page when done."""
    bot = make_bot(db)
    asyncio.run(bot._send_carousel(picks()))
    q = FakeQuery()

    async def slow_caption(caption=None, parse_mode=None, reply_markup=None):
        await asyncio.sleep(0.02)
        q.message.caption_edits.append((caption, reply_markup))
    q.message.edit_caption = slow_caption
    sid = db.carousel_pages(1)[0]["id"]

    async def both():
        await asyncio.gather(bot._cb_accept(q, sid), bot._cb_nav(q, 1, 1))
    asyncio.run(both())
    assert "Ausgewählt von Sam" in q.message.caption_edits[0][0]
    [(media, _)] = q.message.media_edits
    assert "Runner (2026)" in media.caption and db.carousel(1)["position"] == 1


def test_a_decision_for_a_page_no_longer_shown_only_appears_when_paging_back(db):
    bot = make_bot(db)
    asyncio.run(bot._send_carousel(picks()))
    q = FakeQuery()
    asyncio.run(bot._cb_nav(q, 1, 1))
    sid = db.carousel_pages(1)[0]["id"]
    asyncio.run(bot._cb_accept(q, sid))  # a late tap on the first page's button
    assert q.message.caption_edits == []  # page 2 is shown – nothing to redraw there
    asyncio.run(bot._cb_nav(q, 1, 0))
    assert "Ausgewählt von Sam" in q.message.media_edits[-1][0].caption


def test_a_stand_in_poster_is_not_remembered_as_the_poster(db):
    bot = make_bot(db)
    calls = []

    async def send_photo(chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        calls.append(photo)
        if len(calls) == 1:
            raise TelegramError("wrong file identifier")
        return Sent(42, "file-placeholder")
    bot.app.bot.send_photo = send_photo
    asyncio.run(bot._send_carousel(picks()))
    assert len(calls) == 2 and db.carousel_pages(1)[0]["file_id"] is None  # the real poster is tried again
    q = FakeQuery()

    async def edit_media(media, reply_markup=None):
        q.message.media_edits.append((media, reply_markup))
        if len(q.message.media_edits) == 1:
            raise TelegramError("wrong file identifier")
        return Sent(42, "file-placeholder")
    q.message.edit_media = edit_media
    asyncio.run(bot._cb_nav(q, 1, 1))
    assert db.carousel_pages(1)[1]["file_id"] is None and db.carousel(1)["position"] == 1
