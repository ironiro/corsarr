import asyncio
from datetime import timedelta

import pytest

from corsarr import persona
from corsarr.db import iso, now
from corsarr.feedback import FeedbackService
from corsarr.llm import FeedbackIntent, Pick, Selection, SettingsChange, Understanding
from corsarr.models import Candidate
from corsarr.profile import ProfileBuilder, build_profile
from corsarr.recommender import Recommender

TICKS_MIN = 600_000_000


def run(coro):
    return asyncio.run(coro)


def cand(n, source="library", genres=("Thriller",), keywords=(), rating=7.0, media_type="movie"):
    return Candidate(media_type=media_type, source=source, title=f"T{n}", year=2020,
                     jellyfin_id=f"jf{n}" if source == "library" else None, tmdb_id=n,
                     overview="Inhalt", genres=list(genres), keywords=list(keywords), rating=rating,
                     runtime_min=100)


def understanding(intent="recommend", media_types=("movie",)):
    return Understanding(intent=intent, media_types=list(media_types), jellyfin_genres=["Thriller"],
                         tmdb_movie_genre_ids=[53], tmdb_tv_genre_ids=[],
                         settings=SettingsChange(), feedback=FeedbackIntent(rating="none", text=""))


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


class FakeSeerr:
    def __init__(self, new=()):
        self.new = list(new)

    async def discover(self, media_type, genre_ids, exclude, want=15, max_pages=3):
        return [c for c in self.new if c.media_type == media_type and c.key not in exclude]

    async def enrich(self, cands):
        pass

    async def keywords_for(self, media_type, tmdb_id):
        return ["Horror"], ["gore", "slasher"]


class FakeLLM:
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


# --- persona -------------------------------------------------------------------

def test_enforce_single_speaker_prefixes_lines():
    out = persona.enforce("Arrr!\n📱 falsch", "pirate")
    assert out.splitlines() == [f"{persona.PIRATE} Arrr!", f"{persona.PIRATE} falsch"]


def test_enforce_normal_strips_emojis():
    assert persona.enforce(f"{persona.PIRATE} Hallo", "normal") == "Hallo"


def test_choose_speaker_respects_settings():
    assert persona.choose_speaker({"pirate_enabled": False, "genz_enabled": True}) == "genz"
    assert persona.choose_speaker({"pirate_enabled": False, "genz_enabled": False}) == "normal"
    assert persona.choose_speaker({"pirate_enabled": True, "genz_enabled": True}) in {"pirate", "genz", "dialog"}


# --- db -----------------------------------------------------------------------------

def test_settings_roundtrip(db):
    assert db.settings()["series_pause_days"] == 14
    db.set_setting("series_pause_days", 21)
    db.set_setting("pirate_enabled", False)
    assert db.settings()["series_pause_days"] == 21
    assert db.settings()["pirate_enabled"] is False
    with pytest.raises(KeyError):
        db.set_setting("nope", 1)


# --- profile --------------------------------------------------------------------------

def test_profile_traits_push_down_matching_keywords():
    prof = build_profile([{"Genres": ["Horror"]}], [],
                         [{"direction": "less", "trait": "Gore", "keywords": ["gore"], "weight": 1.0}])
    gory = cand(1, genres=["Horror"], keywords=["gore", "zombie"])
    clean = cand(2, genres=["Horror"], keywords=["haunted house"])
    assert prof.score(clean) > prof.score(gory)


def test_profile_feedback_moves_genres():
    fb = [{"rating": -1, "weight": 1.0, "genres": ["Komödie"], "keywords": []}]
    prof = build_profile([], fb, [])
    assert prof.score(cand(1, genres=["Komödie"])) < prof.score(cand(2, genres=["Drama"]))


# --- recommender ------------------------------------------------------------------------

def make_recommender(db, library, new, pick_ids):
    jf, seerr, llm = FakeJellyfin(library), FakeSeerr(new), FakeLLM(pick_ids)
    return Recommender(db, jf, seerr, llm, ProfileBuilder(db, jf)), llm


def test_two_library_titles_first_then_new(db):
    lib = [cand(i) for i in range(1, 8)]
    new = [cand(100 + i, source="new") for i in range(8)]
    rec, llm = make_recommender(db, lib, new, [0, 1, 2, 8, 9, 10])  # model wants 3 library titles
    result = run(rec.recommend("thriller", understanding(), "genz"))
    sources = [c.source for c, _ in result.picks]
    assert sources == ["library", "library", "new", "new", "new", "new"][:len(sources)]
    assert sources.count("library") == 2 and 5 <= len(sources) <= 6


def test_library_fills_in_when_jellyseerr_finds_nothing(db):
    lib = [cand(i) for i in range(1, 8)]
    rec, _ = make_recommender(db, lib, [], [0, 1, 2, 3, 4])
    result = run(rec.recommend("thriller", understanding(), "genz"))
    assert len(result.picks) == 5 and all(c.source == "library" for c, _ in result.picks)


def test_rejected_titles_never_return(db):
    lib = [cand(i) for i in range(1, 7)]
    db.reject(lib[0].key, lib[0].label)
    rec, llm = make_recommender(db, lib, [], [0, 1, 2, 3, 4])
    run(rec.recommend("thriller", understanding(), "genz"))
    assert lib[0].key not in {c.key for c in llm.seen[0]}


def test_accepted_titles_are_not_suggested_again(db):
    lib = [cand(i) for i in range(1, 8)]
    sid = db.add_suggestion(lib[0])
    db.set_suggestion_status(sid, "accepted")
    rec, llm = make_recommender(db, lib, [], [0, 1, 2, 3, 4])
    run(rec.recommend("thriller", understanding(), "genz"))
    assert lib[0].key not in {c.key for c in llm.seen[0]}


def test_new_only_skips_library(db):
    lib = [cand(i) for i in range(1, 8)]
    new = [cand(100 + i, source="new") for i in range(6)]
    rec, llm = make_recommender(db, lib, new, [0, 1, 2, 3, 4])
    result = run(rec.recommend("such neues", understanding(intent="new_only"), "genz"))
    assert all(c.source == "new" for c, _ in result.picks)


# --- feedback -----------------------------------------------------------------------------

def movie_item(played=False, pos_min=0, runtime_min=100):
    return {"Id": "m1", "Type": "Movie", "Name": "Film", "ProductionYear": 2021,
            "ProviderIds": {"Tmdb": "555"}, "RunTimeTicks": runtime_min * TICKS_MIN,
            "UserData": {"Played": played, "PlaybackPositionTicks": pos_min * TICKS_MIN}}


def make_feedback(db, items, season=None):
    jf = FakeJellyfin(items=items, season=season)
    svc = FeedbackService(db, jf, FakeSeerr(), ProfileBuilder(db, jf))
    sent = []

    async def notifier(req):
        sent.append(req)
        db.update_request(req["id"], status="sent", sent_at=now(), message_id=len(sent))
        return True

    svc.notifier = notifier
    return svc, sent


def test_finished_movie_asks_immediately(db):
    svc, sent = make_feedback(db, {"m1": movie_item(played=True)})
    run(svc.on_playback_stop({"event": "PlaybackStop", "itemId": "m1"}))
    assert [r["kind"] for r in sent] == ["movie"]
    run(svc.on_playback_stop({"event": "PlaybackStop", "itemId": "m1"}))
    assert len(sent) == 1  # no duplicate question


def test_abort_is_scheduled_then_asked_and_expires_lightly(db):
    svc, sent = make_feedback(db, {"m1": movie_item(pos_min=30)})
    run(svc.on_playback_stop({"event": "PlaybackStop", "itemId": "m1"}))
    assert sent == []
    req = db.due_requests()  # not yet due
    assert req == []
    rid = db.conn.execute("SELECT id FROM feedback_requests").fetchone()["id"]
    db.update_request(rid, due_at=now() - timedelta(minutes=1))
    run(svc.tick())
    assert [r["kind"] for r in sent] == ["abort"]
    db.update_request(rid, sent_at=now() - timedelta(days=3))
    run(svc.tick())
    fb = db.all_feedback()
    assert len(fb) == 1 and fb[0]["rating"] == -1 and fb[0]["weight"] < 1
    assert db.request(rid)["status"] == "expired"


def test_short_accidental_start_is_ignored(db):
    svc, sent = make_feedback(db, {"m1": movie_item(pos_min=2)})
    run(svc.on_playback_stop({"event": "PlaybackStop", "itemId": "m1"}))
    assert db.conn.execute("SELECT COUNT(*) c FROM feedback_requests").fetchone()["c"] == 0


def test_season_end_detected_from_jellyfin(db):
    series = {"Id": "s1", "Type": "Series", "Name": "Serie", "ProviderIds": {"Tmdb": "77"},
              "UserData": {"Played": False}}
    ep = {"Id": "e3", "Type": "Episode", "SeriesId": "s1", "SeasonId": "se1", "ParentIndexNumber": 1,
          "RunTimeTicks": 45 * TICKS_MIN, "UserData": {"Played": True}}
    season = [{"Id": "e1", "UserData": {"Played": True}}, {"Id": "e2", "UserData": {"Played": True}},
              {"Id": "e3", "UserData": {"Played": False}}]  # webhook may arrive before the flag flips
    svc, sent = make_feedback(db, {"s1": series, "e3": ep}, season=season)
    run(svc.on_playback_stop({"event": "PlaybackStop", "itemId": "e3"}))
    assert [(r["kind"], r["extra"]["season"]) for r in sent] == [("season", 1)]


def test_series_pause_question(db):
    svc, sent = make_feedback(db, {})
    db.touch_watch_state("s1", "tv:77", "Serie", "tv", 1.0, False)
    old = iso(now() - timedelta(days=20))
    db.conn.execute("UPDATE watch_state SET last_activity=?", (old,))
    db.conn.commit()
    run(svc.tick())
    run(svc.tick())
    assert [r["kind"] for r in sent] == ["series_pause"]


def test_store_rating_uses_tmdb_keywords(db):
    svc, _ = make_feedback(db, {})
    rid = db.add_request("movie", "movie:555", "m1", "Film", "movie", now(), status="sent")
    run(svc.store_rating(db.request(rid), "down"))
    fb = db.all_feedback()[0]
    assert fb["keywords"] == ["gore", "slasher"] and fb["rating"] == -1


# --- caption --------------------------------------------------------------------------------

def test_caption_marks_source_and_fits_limit():
    from corsarr.bot import CAPTION_LIMIT, CorsarrBot
    fb = CorsarrBot.__new__(CorsarrBot)
    new = cand(1, source="new")
    new.overview = "Sehr langer Inhalt " * 200
    cap = fb._caption(new, "📱 passt, weil lowkey spooky")
    assert "Nicht vorhanden" in cap and "Stunden" in cap and len(cap) <= CAPTION_LIMIT
    lib = cand(2, media_type="tv")
    lib.seasons = 3
    cap = fb._caption(lib, "")
    assert "In eurer Bibliothek · Serie" in cap and "3 Staffeln" in cap and "pro Folge" in cap


class FakeMessage:
    photo = None
    text_html = "<b>T1 (2020)</b>"
    reply_markup = None

    def __init__(self):
        self.edits = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs.get("reply_markup")))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMessage()
        self.message.chat_id = -100
        self.from_user = type("U", (), {"first_name": "Sam"})()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


def test_accept_button_marks_card_once(db):
    from corsarr.bot import CorsarrBot
    fb = CorsarrBot.__new__(CorsarrBot)
    fb.db = db
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    sid = db.add_suggestion(cand(1))
    q = FakeQuery(f"acc:{sid}")
    trailer = InlineKeyboardButton("🎬 Trailer", url="https://www.youtube.com/watch?v=x")
    q.message.reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅", callback_data=f"acc:{sid}"), InlineKeyboardButton("🙅", callback_data="rej:1")],
        [trailer]])
    run(fb._cb_accept(q, sid))
    assert db.suggestion(sid)["status"] == "accepted"
    text, markup = q.message.edits[0]
    assert "Ausgewählt von Sam" in text
    assert [[b.text for b in row] for row in markup.inline_keyboard] == [["🎬 Trailer"]]  # only the link stays
    run(fb._cb_accept(q, sid))
    assert q.answers[-1] == "Ist schon ausgewählt ✅" and len(q.message.edits) == 1


def test_genre_request_ranks_matches_first_and_does_not_pad_with_others(db):
    lib = [cand(1, genres=["Liebesfilm"]), cand(2, genres=["Horror", "Thriller"]), cand(3, genres=["Komödie"]),
           cand(4, genres=["Thriller"]), cand(5, genres=["Drama"]), cand(6, genres=["Doku"])]
    rec, llm = make_recommender(db, lib, [], [0])  # model picks only the best fit
    und = understanding()
    und.jellyfin_genres = ["Horror", "Thriller"]
    result = run(rec.recommend("horror oder thriller", und, "genz"))
    assert [c.title for c in llm.seen[0][:2]] == ["T2", "T4"]  # both genres before one genre
    assert {c.title for c, _ in result.picks} <= {"T2", "T4"}  # never padded with romance/comedy


def test_watch_history_reaches_the_model(db):
    lib = [cand(i) for i in range(1, 7)]
    rec, llm = make_recommender(db, lib, [], [0, 1, 2, 3, 4])
    rec.profiles.jellyfin.taste_items = lambda: _history()
    run(rec.recommend("was passt zu uns", understanding(), "genz"))
    assert llm.taste["liked"] == ["Hereditary (2018)"] and llm.taste["recent_movies"] == ["Midsommar (2019)"]
    assert llm.taste["recent_series"] == ["Dark (2017)"]  # series are not crowded out by many movies


async def _history():
    return [{"Name": "Midsommar", "ProductionYear": 2019, "Genres": ["Horror"]},
            {"Name": "Dark", "ProductionYear": 2017, "Type": "Series", "Genres": ["Mystery"]},
            {"Name": "Hereditary", "ProductionYear": 2018, "Genres": ["Horror"], "_favorite": True}]


def test_discover_sends_no_language_filter_but_a_vote_minimum():
    import httpx
    from corsarr.jellyseerr import MIN_VOTES, Jellyseerr
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"results": [], "totalPages": 1})

    seerr = Jellyseerr("http://seerr", "key")
    seerr.http = httpx.AsyncClient(base_url="http://seerr/api/v1", transport=httpx.MockTransport(handler))
    run(seerr.discover("movie", [27, 53], set()))
    assert "language" not in seen[0]  # would restrict to German-made films
    assert seen[0]["genre"] == "27|53" and seen[0]["voteCountGte"] == str(MIN_VOTES)


def test_feedback_question_always_names_the_title(db):
    from corsarr.bot import CorsarrBot
    sent = []

    class Tg:
        async def send_message(self, chat_id, text, **kwargs):
            sent.append((text, kwargs))
            return type("M", (), {"message_id": 5})()

    class Llm:
        async def say(self, situation, speaker, facts=None):
            return "📱 und, wie war's? <3"  # the model forgot the title (and sends HTML-ish text)

    fb = CorsarrBot.__new__(CorsarrBot)
    fb.db, fb.llm, fb.down = db, Llm(), False
    fb.cfg = type("Cfg", (), {"chat_id": -100})()
    fb.app = type("App", (), {"bot": Tg()})()
    rid = db.add_request("season", "tv:83867", "s1", "Andor (2022)", "tv", now(), status="pending",
                         extra={"season": 1, "series_done": False})
    assert run(fb.ask_feedback(db.request(rid)))
    text, kwargs = sent[0]
    assert text.startswith("📺 <b>Andor (2022)</b> · Staffel 1\n\n")
    assert text.endswith("wie war's? &lt;3") and kwargs["parse_mode"] == "HTML"


# --- title lookup ("there's a new Marvel series with Vision") -------------------------------------

class SearchSeerr(FakeSeerr):
    def __init__(self, by_query):
        super().__init__()
        self.by_query, self.queries, self.enriched = by_query, [], []

    async def search(self, query):
        self.queries.append(query)
        if query == "broken":
            raise RuntimeError("HTTP 500")
        return list(self.by_query.get(query, []))

    async def enrich(self, cands):
        self.enriched += cands


class IdentifyLLM(FakeLLM):
    async def identify(self, request, cands, speaker):
        self.seen = cands
        return Selection(intro="Das ist VisionQuest.", picks=[Pick(id=i, reason="Marvel, mit Vision") for i in self.pick_ids])


def test_lookup_searches_each_term_and_shows_only_the_meant_hits(db):
    quest = cand(1, source="new", media_type="tv")
    wanda = cand(2, source="library", media_type="tv")
    other = cand(3, source="new", media_type="movie")
    seerr = SearchSeerr({"VisionQuest": [quest], "Vision": [wanda, quest, other]})
    llm = IdentifyLLM([0, 0, 7])  # a duplicate and an id out of range are ignored
    rec = Recommender(db, FakeJellyfin(), seerr, llm, ProfileBuilder(db, FakeJellyfin()))
    und = understanding(intent="lookup", media_types=("tv",))
    und.search_queries = ["VisionQuest", "Vision", "VisionQuest", "broken"]
    result = run(rec.lookup("es gibt doch jetzt eine serie mit vision von marvel", und, "normal"))
    assert seerr.queries == ["VisionQuest", "Vision", "broken"]  # duplicates searched once, a failure is skipped
    assert [c.key for c in llm.seen] == [quest.key, wanda.key]  # deduplicated, only series
    assert [(c.key, r) for c, r in result.picks] == [(quest.key, "Marvel, mit Vision")]
    assert seerr.enriched == [quest] and result.intro == "Das ist VisionQuest."


def test_lookup_without_hits_returns_nothing(db):
    rec = Recommender(db, FakeJellyfin(), SearchSeerr({}), IdentifyLLM([0]), ProfileBuilder(db, FakeJellyfin()))
    und = understanding(intent="lookup", media_types=())
    result = run(rec.lookup("der film wo er am ende merkt dass er tot ist", und, "normal"))
    assert result.picks == []


def test_seerr_search_marks_library_requested_and_new():
    import httpx
    from corsarr.jellyseerr import Jellyseerr

    def handler(request):
        assert request.url.path == "/api/v1/search" and request.url.params["query"] == "The Sixth Sense"
        return httpx.Response(200, json={"results": [
            {"id": 745, "mediaType": "movie", "title": "The Sixth Sense", "releaseDate": "1999-08-06",
             "overview": "x", "voteCount": 12000, "mediaInfo": {"status": 5}},
            {"id": 1, "mediaType": "tv", "name": "Sixth Sense Show", "firstAirDate": "2020-01-01", "mediaInfo": {"status": 3}},
            {"id": 2, "mediaType": "movie", "title": "Other", "releaseDate": ""},
            {"id": 3, "mediaType": "person", "name": "M. Night Shyamalan"},
        ]})
    seerr = Jellyseerr("http://seerr:5055", "k")
    seerr.http = httpx.AsyncClient(base_url="http://seerr:5055/api/v1", transport=httpx.MockTransport(handler))
    hits = run(seerr.search("The Sixth Sense"))
    assert [(c.title, c.year, c.source) for c in hits] == [
        ("The Sixth Sense", 1999, "library"), ("Sixth Sense Show", 2020, "pending"), ("Other", None, "new")]
    assert hits[0].votes == 12000
