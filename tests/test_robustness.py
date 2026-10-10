"""Nothing is left hanging: every message gets an answer, API errors are mapped, odd rows don't block jobs."""
import asyncio
import logging

import anthropic
import httpx
import pytest

from corsarr import arr
from corsarr.llm import LLM, LLMFailed, LLMUnavailable, _Claude, _OpenAICompatible
from corsarr.models import Candidate
from helpers import FakeMessage, FakeQuery, NoTags, Profiles, RequestSeerr, SayLLM, age_imports, make_bot, run, sonarr


# --- B4: a message always gets an answer -------------------------------------------------------------

def test_unexpected_errors_still_get_the_generic_reply(db):
    bot = make_bot(db)

    class BrokenLLM(SayLLM):
        async def understand(self, text, recent):
            raise RuntimeError("boom")
    bot.llm = BrokenLLM()
    ctx = type("Ctx", (), {"bot": bot.app.bot})()

    async def typing(chat_id, action):
        pass
    bot.app.bot.send_chat_action = typing
    msg = FakeMessage()
    run(bot._handle(msg, "something", ctx))
    assert msg.replies == ["🤷 Das hat gerade nicht geklappt – formuliert es bitte noch einmal anders."]


def test_a_failing_recovery_post_does_not_escape_and_recovery_is_posted_once(db):
    bot = make_bot(db)
    bot.down = True
    pings = []

    class Llm(SayLLM):
        label = "Claude"

        async def ping(self):
            pings.append(1)
            await asyncio.sleep(0.01)
    bot.llm = Llm()

    async def twice():
        await asyncio.gather(bot._probe(), bot._probe())  # the job and a message at the same time
    run(twice())
    assert len(pings) == 1 and len(bot.app.bot.messages) == 1  # "back again" once

    bot.down = True

    async def failing_post(chat_id, text, **kwargs):
        from telegram.error import TelegramError
        raise TelegramError("flood")
    bot.app.bot.send_message = failing_post
    msg = FakeMessage()
    run(bot._handle(msg, "hi", type("Ctx", (), {"bot": bot.app.bot})()))  # no exception reaches the caller
    assert bot.down is False


# --- B10/B11: buttons ---------------------------------------------------------------------------------

def test_already_requested_in_jellyseerr_counts_as_requested(db):
    seerr = RequestSeerr(fail={3: 409})
    bot = make_bot(db, seerr)
    sid = db.add_suggestion(Candidate(media_type="movie", source="new", title="Part 3", year=2000, tmdb_id=3))
    q = FakeQuery()
    run(bot._cb_request(q, sid))
    assert db.suggestion(sid)["status"] == "requested" and q.answers == [None]  # answered right away
    assert bot.llm.said[0]["title"] == "Part 3 (2000)"  # the character still comments

    seerr = RequestSeerr(fail={4: 500})
    bot = make_bot(db, seerr)
    sid = db.add_suggestion(Candidate(media_type="movie", source="new", title="Part 4", year=2000, tmdb_id=4))
    run(bot._cb_request(FakeQuery(), sid))
    assert db.suggestion(sid)["status"] == "suggested"
    assert bot.app.bot.messages[-1][0] == "<b>Part 4 (2000)</b> – Anfrage bei Jellyseerr fehlgeschlagen ❌"


def test_unknown_rating_values_from_callback_data_are_ignored(db):
    from corsarr.db import now
    bot = make_bot(db, feedback=NoTags(db, None, None, Profiles()))
    rid = db.add_request("movie", "movie:9", "jf9", "Heat (1995)", "movie", now(), status="sent")
    q = FakeQuery()
    run(bot._cb_rating(q, rid, "sideways"))
    assert db.all_feedback() == [] and q.answers == ["Schon beantwortet"]


def test_a_failing_comment_after_a_button_is_only_logged(db, caplog):
    class NoComment(SayLLM):
        label = "Claude"

        async def say(self, situation, speaker, facts=None):
            raise LLMFailed("refusal")
    bot = make_bot(db)
    bot.llm = NoComment()
    sid = db.add_suggestion(Candidate(media_type="movie", source="new", title="Part 3", year=2000, tmdb_id=3))
    q = FakeQuery()
    q.data = f"req:{sid}"
    with caplog.at_level(logging.WARNING):
        run(bot.on_callback(type("Upd", (), {"callback_query": q})(), None))
    assert db.suggestion(sid)["status"] == "requested" and "refusal" in caplog.text


# --- B3: API errors of the language model ----------------------------------------------------------------

def _status_error(cls, status, message):
    resp = httpx.Response(status, request=httpx.Request("POST", "http://x"))
    return cls(message, response=resp, body={"error": {"type": "e", "message": message}})


def _claude_raising(error):
    backend = _Claude("sk-test", "claude-nope")

    async def create(**kwargs):
        raise error
    backend.client.messages.create = create
    return LLM(backend)


def test_claude_errors_are_mapped_instead_of_escaping():
    with pytest.raises(LLMUnavailable, match="claude-nope"):
        run(_claude_raising(_status_error(anthropic.NotFoundError, 404, "model: claude-nope")).say("s", "normal"))
    with pytest.raises(LLMFailed, match="too long"):
        run(_claude_raising(_status_error(anthropic.BadRequestError, 400, "prompt is too long")).say("s", "normal"))
    with pytest.raises(LLMUnavailable, match="credit balance"):
        run(_claude_raising(_status_error(anthropic.BadRequestError, 400, "Your credit balance is too low")).say("s", "normal"))
    with pytest.raises(LLMFailed):
        run(_claude_raising(_status_error(anthropic.UnprocessableEntityError, 422, "nope")).say("s", "normal"))
    with pytest.raises(LLMUnavailable):
        run(_claude_raising(_status_error(anthropic.APIStatusError, 529, "overloaded")).say("s", "normal"))


def test_openai_compatible_404_names_the_model():
    backend = _OpenAICompatible("ollama", "llama-nope", "", "http://ollama:11434")
    backend.client = httpx.AsyncClient(base_url="http://ollama:11434/v1", transport=httpx.MockTransport(
        lambda request: httpx.Response(404, json={"error": {"message": "model not found"}})))
    with pytest.raises(LLMUnavailable, match="llama-nope"):
        run(LLM(backend).say("s", "normal"))


# --- B9: download notifications ------------------------------------------------------------------------------

def test_missing_episode_numbers_and_a_bad_row_do_not_block_the_other_messages(db, caplog):
    aired = "2099-01-01T01:00:00Z"
    arr.record_sonarr(db, sonarr([{"seasonNumber": None, "episodeNumber": None, "title": "Pilot", "airDateUtc": aired}],
                                 title="Odd", series_id=1))
    arr.record_sonarr(db, sonarr([{"seasonNumber": 1, "episodeNumber": 2, "title": "Two", "airDateUtc": aired}],
                                 title="Fine", series_id=2))
    age_imports(db, 3)
    db.conn.execute("UPDATE arr_imports SET created_at='garbage' WHERE group_key='sonarr:2'")  # a broken row
    db.conn.commit()
    with caplog.at_level(logging.ERROR):
        messages = arr.due_messages(db)
    assert [text for _, text in messages] == ["📺 Odd (2005): S00E00 „Pilot“ ist da"]
    assert "skipped" in caplog.text
    assert [r["group_key"] for r in db.pending_imports()] == ["sonarr:1"]  # the bad row is marked handled
