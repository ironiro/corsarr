"""Feedback questions under real conditions: two people rating at once, the job and the webhook sending the
same question, questions that can't be posted, and the light thumbs down of an ignored abort question."""
import asyncio
from datetime import timedelta

from corsarr import feedback as fb_mod
from corsarr.db import iso, now
from corsarr.feedback import FeedbackService
from corsarr.profile import ProfileBuilder
from helpers import FakeJellyfin, FakeSeerr, make_feedback, movie_item, run


class SlowTags(FeedbackService):
    """Fetching a title's tags takes a moment – the window in which the other person's rating lands."""
    async def title_tags(self, req):
        await asyncio.sleep(0.01)
        return ["Horror"], ["gore"]


def test_two_people_rating_at_the_same_time_both_persist(db):
    svc = SlowTags(db, FakeJellyfin(), FakeSeerr(), ProfileBuilder(db, FakeJellyfin()))
    rid = db.add_request("movie", "movie:9", "jf9", "Heat (1995)", "movie", now(), status="sent")

    async def both():
        await asyncio.gather(svc.store_rating(db.request(rid), "up", rater=(1, "Sam")),
                             svc.store_rating(db.request(rid), "down", rater=(2, "Alex")))
    run(both())
    ratings = db.request(rid)["extra"]["ratings"]
    assert {r["name"]: r["rating"] for r in ratings.values()} == {"Sam": "up", "Alex": "down"}  # no lost update
    assert len(db.all_feedback()) == 2 and db.request(rid)["extra"]["first_rated_at"]


def test_the_others_get_a_day_from_the_first_rating_not_from_the_question(db):
    svc, _ = make_feedback(db, {})
    rid = db.add_request("movie", "movie:9", "jf9", "Heat (1995)", "movie", now(), status="sent",
                         extra={})
    db.update_request(rid, sent_at=now() - timedelta(days=3))  # asked long ago …
    run(svc.store_rating(db.request(rid), "up", rater=(1, "Sam")))  # … rated just now
    run(svc.tick())
    assert db.request(rid)["status"] == "sent"  # Alex still has time
    extra = {**db.request(rid)["extra"], "first_rated_at": iso(now() - timedelta(days=2))}
    db.update_request(rid, extra=extra)
    run(svc.tick())
    assert db.request(rid)["status"] == "answered"


def test_an_ignored_abort_question_does_not_block_the_real_question_or_recommendations(db):
    svc, sent = make_feedback(db, {"m1": movie_item(pos_min=30)})
    run(svc.on_playback_stop({"itemId": "m1"}))
    rid = db.conn.execute("SELECT id FROM feedback_requests").fetchone()["id"]
    db.update_request(rid, status="sent", sent_at=now() - timedelta(days=3))
    run(svc.tick())  # expired: light thumbs down
    assert db.request(rid)["status"] == "expired" and db.all_feedback()[0]["weight"] < 1
    assert "movie:555" in db.feedback_keys() and "movie:555" not in db.feedback_keys(min_weight=1.0)
    # They finish the film after all: the automatic rating goes, the real question is asked.
    svc.jellyfin._items["m1"] = movie_item(played=True)
    run(svc.on_playback_stop({"itemId": "m1"}))
    assert [r["kind"] for r in sent] == ["movie"] and db.all_feedback() == []


def test_job_and_webhook_post_a_pending_question_only_once(db):
    svc, _ = make_feedback(db, {})
    posted = []

    async def slow_notifier(req):
        await asyncio.sleep(0.01)
        posted.append(req["id"])
        db.update_request(req["id"], status="sent", sent_at=now())
        return True
    svc.notifier = slow_notifier
    rid = db.add_request("movie", "movie:9", "jf9", "Heat (1995)", "movie", now(), status="pending")

    async def both():
        await asyncio.gather(svc._send(rid), svc.tick())
    run(both())
    assert posted == [rid] and db.request(rid)["status"] == "sent"


def test_a_question_that_cannot_be_posted_is_given_up_without_endless_retries(db):
    svc, _ = make_feedback(db, {})
    tries = []

    async def failing(req):
        tries.append(req["id"])
        return False
    svc.notifier = failing
    rid = db.add_request("movie", "movie:9", "jf9", "Heat (1995)", "movie", now(), status="pending")
    for _ in range(fb_mod.SEND_ATTEMPTS + 3):
        run(svc.tick())
    assert len(tries) == fb_mod.SEND_ATTEMPTS and db.request(rid)["status"] == "expired"

    # A claim nobody gave back (crash while posting) is retried after a while.
    rid2 = db.add_request("movie", "movie:10", "jf10", "Alien (1979)", "movie", now(), status="sending")
    db.update_request(rid2, sent_at=now() - timedelta(hours=1))
    run(svc.tick())  # given back …
    assert db.request(rid2)["status"] == "pending"
    run(svc.tick())  # … and tried again
    assert tries[-1] == rid2
