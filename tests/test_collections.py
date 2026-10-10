"""All parts of a film series: find the TMDB collection, show each part's status, request the missing ones."""
import asyncio

import httpx

from corsarr.bot import CorsarrBot
from corsarr.jellyseerr import Jellyseerr
from corsarr.llm import FeedbackIntent, Pick, Selection, SettingsChange, Understanding
from corsarr.models import Candidate, FilmCollection
from corsarr.profile import ProfileBuilder
from corsarr.recommender import Recommender


def run(coro):
    return asyncio.run(coro)


def movie(n, source="new", title=None, year=2000):
    return Candidate(media_type="movie", source=source, title=title or f"Part {n}", year=year, tmdb_id=n,
                     overview="Plot.", poster_url=f"https://image.tmdb.org/t/p/w500/p{n}.jpg")


def understanding(whole_collection=True):
    return Understanding(intent="lookup", whole_collection=whole_collection, search_queries=["Example Saga"],
                         media_types=[], jellyfin_genres=[], tmdb_movie_genre_ids=[], tmdb_tv_genre_ids=[],
                         settings=SettingsChange(), feedback=FeedbackIntent(rating="none", text=""))


# --- Jellyseerr client ------------------------------------------------------------------------------

def test_collection_endpoint_maps_status_per_part():
    def handler(request):
        assert request.url.path == "/api/v1/collection/119"
        return httpx.Response(200, json={"id": 119, "name": "Example Saga", "posterPath": "/c.jpg", "parts": [
            {"id": 1, "mediaType": "movie", "title": "Saga One", "releaseDate": "2001-01-01",
             "mediaInfo": {"status": 5}},
            {"id": 2, "mediaType": "movie", "title": "Saga Two", "releaseDate": "2002-01-01",
             "mediaInfo": {"status": 3}},
            {"id": 3, "mediaType": "movie", "title": "Saga Three", "releaseDate": "2003-01-01"},
            {"id": 4, "mediaType": "movie", "title": "Saga Four", "releaseDate": "", "mediaInfo": {"status": 7}},
            {"id": 5, "mediaType": "movie", "title": "Saga Five", "mediaInfo": {"status": 6}},
        ]})
    seerr = Jellyseerr("http://seerr:5055", "k")
    seerr.http = httpx.AsyncClient(base_url="http://seerr:5055/api/v1", transport=httpx.MockTransport(handler))
    coll = run(seerr.collection(119))
    assert (coll.tmdb_id, coll.name, coll.poster_url) == (119, "Example Saga", "https://image.tmdb.org/t/p/w500/c.jpg")
    assert [(c.label, c.source) for c in coll.parts] == [
        ("Saga One (2001)", "library"), ("Saga Two (2002)", "pending"), ("Saga Three (2003)", "new"),
        ("Saga Four", "new"), ("Saga Five", "blocked")]  # deleted counts as missing, blocklisted can't be requested


# --- recommender: lookup with whole_collection ---------------------------------------------------------

class CollectionSeerr:
    def __init__(self, details, collection=None):
        self.details_by_id, self.coll = details, collection
        self.enriched, self.asked = [], []

    async def search(self, query):
        return [movie(2, title="Saga Two"), Candidate(media_type="tv", source="new", title="Saga Show", tmdb_id=9)]

    async def enrich(self, cands):
        self.enriched += cands

    async def details(self, media_type, tmdb_id):
        return self.details_by_id.get(tmdb_id, {})

    async def collection(self, collection_id):
        self.asked.append(collection_id)
        return self.coll


class LookupLLM:
    def __init__(self, pick_ids):
        self.pick_ids, self.said = pick_ids, []

    async def identify(self, request, cands, speaker):
        return Selection(intro="That's Saga Two.", picks=[Pick(id=i, reason="the one") for i in self.pick_ids])

    async def say(self, situation, speaker, facts=None):
        self.said.append((situation, facts))
        return "🏴‍☠️ The whole saga!"


def make_recommender(db, seerr, llm):
    return Recommender(db, None, seerr, llm, ProfileBuilder(db, None))


def saga():
    return FilmCollection(tmdb_id=119, name="Example Saga", parts=[
        movie(1, "library"), movie(2, "pending"), movie(3, "new"), movie(4, "new")])


def test_whole_collection_finds_the_series_of_the_meant_movie(db):
    seerr = CollectionSeerr({2: {"collection": {"id": 119, "name": "Example Saga", "posterPath": "/c.jpg"}}}, saga())
    llm = LookupLLM([1, 0])  # the series hit first: the first *movie* decides the collection
    rec = run(make_recommender(db, seerr, llm).lookup("get us all Saga films", understanding(), "pirate"))
    assert seerr.asked == [119] and rec.collection.name == "Example Saga"
    assert rec.collection.poster_url == "https://image.tmdb.org/t/p/w500/c.jpg"  # from the details' collection
    assert rec.intro == "🏴‍☠️ The whole saga!" and rec.library_count == 1
    facts = llm.said[0][1]
    assert facts["missing"] == ["Part 3 (2000)", "Part 4 (2000)"] and facts["requested"] == ["Part 2 (2000)"]
    assert seerr.enriched == []  # no single card – nothing to enrich


def test_without_a_collection_the_single_title_card_stays(db):
    seerr = CollectionSeerr({2: {"collection": None}})
    llm = LookupLLM([0])
    rec = run(make_recommender(db, seerr, llm).lookup("get us all Saga films", understanding(), "normal"))
    assert rec.collection is None and seerr.asked == []
    assert [c.tmdb_id for c, _ in rec.picks] == [2] and rec.intro == "That's Saga Two."
    assert [c.tmdb_id for c in seerr.enriched] == [2] and llm.said == []


def test_plain_lookup_does_not_look_for_a_collection(db):
    seerr = CollectionSeerr({2: {"collection": {"id": 119}}}, saga())
    rec = run(make_recommender(db, seerr, LookupLLM([0])).lookup("Saga Two?", understanding(False), "normal"))
    assert rec.collection is None and seerr.asked == []


# --- bot: the message and its button -------------------------------------------------------------------

class Sent:
    message_id = 77
    photo = None


class FakeTelegram:
    def __init__(self):
        self.photos, self.messages = [], []

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None, reply_markup=None):
        self.photos.append((photo, caption, reply_markup))
        return Sent()

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((text, kwargs))
        return Sent()


class FakeMessage:
    message_id = 77
    chat_id = -100

    def __init__(self):
        self.caption_edits = []

    async def edit_caption(self, caption=None, parse_mode=None, reply_markup=None):
        self.caption_edits.append((caption, reply_markup))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.from_user = type("U", (), {"first_name": "Sam"})()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs.get("show_alert", False)))


class RequestSeerr:
    def __init__(self, fail=()):
        self.fail, self.requested = dict(fail), []

    async def request(self, media_type, tmdb_id):
        self.requested.append((media_type, tmdb_id))
        if tmdb_id in self.fail:
            req = httpx.Request("POST", "http://seerr/api/v1/request")
            raise httpx.HTTPStatusError("x", request=req, response=httpx.Response(self.fail[tmdb_id], request=req))
        return {"id": tmdb_id}


class SayLLM:
    def __init__(self):
        self.said = []

    async def say(self, situation, speaker, facts=None):
        self.said.append(facts)
        return "📱 on its way"


def make_bot(db, seerr=None):
    bot = CorsarrBot.__new__(CorsarrBot)
    bot.db, bot.seerr, bot.llm = db, seerr or RequestSeerr(), SayLLM()
    bot.cfg = type("Cfg", (), {"chat_id": -100})()
    bot.app = type("App", (), {"bot": FakeTelegram()})()
    bot.speaker = lambda: "normal"
    return bot


def labels(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard] if markup else []


def test_one_message_lists_every_part_with_its_status(db):
    bot = make_bot(db)
    coll = saga()
    coll.poster_url = "https://image.tmdb.org/t/p/w500/c.jpg"
    run(bot._send_collection(coll, "The whole saga!"))
    [(photo, caption, markup)] = bot.app.bot.photos
    assert photo == "https://image.tmdb.org/t/p/w500/c.jpg"
    assert caption == ("The whole saga!\n\n<b>Example Saga</b>\n✅ Part 1 (2000)\n"
                       "⏳ Part 2 (2000) · angefragt, wird geladen\n🆕 Part 3 (2000)\n🆕 Part 4 (2000)")
    assert labels(markup) == [["📥 Fehlende anfragen (2)"]]
    data = markup.inline_keyboard[0][0].callback_data
    assert data == "col:1" and len(data.encode()) <= 64
    assert db.collection(1)["message_id"] == 77


def test_button_requests_only_the_missing_parts_and_shows_who(db):
    seerr = RequestSeerr()
    bot = make_bot(db, seerr)
    run(bot._send_collection(saga(), "intro"))
    q = FakeQuery("col:1")
    run(bot.on_callback(type("Upd", (), {"callback_query": q})(), None))
    assert seerr.requested == [("movie", 3), ("movie", 4)]  # not the library part, not the pending one
    assert db.was_requested("movie", 3) and db.was_requested("movie", 4)  # Radarr note "via Corsarr" works
    assert not db.was_requested("movie", 1)
    assert q.answers == [("2 angefragt ✅", False)]
    caption, markup = q.message.caption_edits[0]
    assert "📥 Part 3 (2000) · angefragt von Sam" in caption and "📥 Part 4 (2000) · angefragt von Sam" in caption
    assert markup is None  # nothing missing any more: the button is gone
    assert bot.llm.said[0]["titles"] == ["Part 3 (2000)", "Part 4 (2000)"]
    assert bot.app.bot.messages[0][1]["reply_to_message_id"] == 77
    q2 = FakeQuery("col:1")
    run(bot._cb_collection(q2, 1))  # pressed again (or by the other person at the same time)
    assert q2.answers == [("Es fehlt nichts mehr ✅", False)] and len(seerr.requested) == 2


def test_a_failed_part_stays_missing_and_an_existing_request_counts(db):
    seerr = RequestSeerr(fail={3: 500, 4: 409})  # 409 = already requested in Jellyseerr itself
    bot = make_bot(db, seerr)
    run(bot._send_collection(saga(), ""))
    q = FakeQuery("col:1")
    run(bot._cb_collection(q, 1))
    assert q.answers == [("1 Anfragen bei Jellyseerr fehlgeschlagen ❌", True)]
    assert not db.was_requested("movie", 3) and db.was_requested("movie", 4)
    caption, markup = q.message.caption_edits[0]
    assert caption.startswith("<b>Example Saga</b>")  # empty intro: no blank line on top
    assert "🆕 Part 3 (2000)" in caption and labels(markup) == [["📥 Fehlende anfragen (1)"]]
    run(bot._cb_collection(FakeQuery("col:1"), 1))  # trying again requests only what is still missing
    assert seerr.requested[-1] == ("movie", 3) and len(seerr.requested) == 3


def test_nothing_missing_means_no_button(db):
    bot = make_bot(db)
    coll = FilmCollection(tmdb_id=5, name="Done Saga", parts=[movie(1, "library"), movie(2, "pending"),
                                                               movie(3, "blocked")])
    run(bot._send_collection(coll, "You have it all."))
    [(photo, caption, markup)] = bot.app.bot.photos
    assert markup is None and "🚫 Part 3 (2000) · gesperrt" in caption
    assert photo.startswith(b"\x89PNG")  # no poster: stand-in image


def test_very_long_series_is_cut_to_fit_the_caption(db):
    bot = make_bot(db)
    coll = FilmCollection(tmdb_id=6, name="Long Saga", parts=[
        movie(i, "new", title=f"A rather long title for part number {i}") for i in range(1, 60)])
    run(bot._send_collection(coll, "intro " * 50))
    [(_, caption, markup)] = bot.app.bot.photos
    assert len(caption) <= 1024 and "weitere" in caption and "intro" not in caption
    assert labels(markup) == [["📥 Fehlende anfragen (59)"]]  # the button still covers every missing part


def test_lookup_reply_uses_the_collection_message(db):
    bot = make_bot(db)

    class Rec:
        async def lookup(self, text, und, speaker):
            from corsarr.recommender import Recommendation
            return Recommendation(intro="Saga time.", picks=[(movie(2), "")], library_count=1, collection=saga())

    class Msg:
        replies = []

        async def reply_text(self, text, **kwargs):
            self.replies.append(text)

    bot.recommender = Rec()
    msg = Msg()
    run(bot._lookup(msg, "all Saga films", understanding()))
    assert msg.replies == [] and len(bot.app.bot.photos) == 1  # one message: intro sits in the caption
    assert bot.app.bot.photos[0][1].startswith("Saga time.")
