"""Ratings per person: everyone rates, the question stays open until all did, and the taste is per person."""
import asyncio

from corsarr.bot import CorsarrBot
from corsarr.db import now
from corsarr.feedback import FeedbackService
from corsarr.models import Candidate
from corsarr.profile import build_profile


class NoTags(FeedbackService):
    async def title_tags(self, req):
        return ["Horror"], ["gore"]


class Profiles:
    def invalidate(self):
        pass


class User:
    def __init__(self, uid, name):
        self.id, self.first_name, self.full_name, self.is_bot = uid, name, name, False


class Msg:
    def __init__(self, text):
        self.text_html, self.reply_markup, self.edits = text, "KEYBOARD", []

    async def edit_text(self, text, **kwargs):
        self.text_html, self.reply_markup = text, kwargs.get("reply_markup")
        self.edits.append(text)


class Query:
    def __init__(self, user, msg):
        self.from_user, self.message, self.answers = user, msg, []

    async def answer(self, text=None):
        self.answers.append(text)


def make_bot(db):
    bot = CorsarrBot.__new__(CorsarrBot)
    bot.db = db
    bot.feedback = NoTags(db, None, None, Profiles())
    return bot


def test_both_rate_the_question_stays_open_until_then_and_rating_again_replaces(db):
    bot = make_bot(db)
    sam, alex = User(1, "Sam"), User(2, "Alex")
    bot._note_member(sam), bot._note_member(alex)
    rid = db.add_request("movie", "movie:9", "jf9", "Heat (1995)", "movie", now(), status="sent")
    msg = Msg("🎬 <b>Heat (1995)</b>\n\nhow was it?")

    asyncio.run(bot._cb_rating(Query(sam, msg), rid, "up"))
    assert db.request(rid)["status"] == "sent" and msg.reply_markup == "KEYBOARD"  # Alex hasn't rated
    assert msg.text_html.endswith("🗳️ 👍 Sam")
    asyncio.run(bot._cb_rating(Query(sam, msg), rid, "meh"))  # Sam changes their mind
    assert msg.text_html.endswith("🗳️ 😐 Sam") and msg.text_html.count("🗳️") == 1
    asyncio.run(bot._cb_rating(Query(alex, msg), rid, "down"))
    assert db.request(rid)["status"] == "answered" and msg.reply_markup is None
    assert msg.text_html.endswith("🗳️ 😐 Sam · 👎 Alex")
    rows = db.all_feedback()
    assert sorted((r["rater_name"], r["rating"]) for r in rows) == [("Alex", -1), ("Sam", 0)]  # replaced, not added


def test_a_single_known_person_closes_the_question_alone(db):
    bot = make_bot(db)
    rid = db.add_request("movie", "movie:9", "jf9", "Heat (1995)", "movie", now(), status="sent")
    asyncio.run(bot._cb_rating(Query(User(1, "Sam"), Msg("q")), rid, "up"))
    assert db.request(rid)["status"] == "answered"


def cand(n, keywords):
    return Candidate(media_type="movie", source="new", title=f"T{n}", tmdb_id=n, genres=["Horror"],
                     keywords=keywords, overview="x")


def test_what_one_person_dislikes_drops_further_than_the_average():
    gore = {"direction": "less", "trait": "Gore", "keywords": ["gore"], "weight": 1.0}
    traits = [{**gore, "rater_name": "Alex"}]
    feedback = [{"title": "Saw (2004)", "rating": 1, "weight": 1.0, "genres": ["Horror"], "keywords": ["gore"],
                 "rater_name": "Sam"},
                {"title": "Hereditary (2018)", "rating": 1, "weight": 1.0, "genres": ["Horror"], "keywords": [],
                 "rater_name": "Alex"}]
    prof = build_profile([], feedback, traits)
    assert set(prof.people) == {"Sam", "Alex"}
    assert prof.score(cand(1, ["haunted house"])) > prof.score(cand(2, ["gore"]))  # Alex: less gore wins
    taste = prof.taste_for_prompt()
    assert taste["per_person"]["Alex"]["less_of"] == ["Gore"] and taste["per_person"]["Sam"]["liked"] == ["Saw (2004)"]


def test_old_ratings_without_a_name_count_for_everyone():
    prof = build_profile([], [{"title": "Old", "rating": -1, "weight": 1.0, "genres": ["Komödie"], "keywords": []}], [])
    assert prof.people == {} and "per_person" not in prof.taste_for_prompt() and prof.genres["komödie"] < 0
