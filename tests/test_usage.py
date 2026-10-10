"""Usage tracking, cost estimate and the monthly budget."""
import asyncio
from datetime import timedelta

import pytest

from corsarr import config, llm, usage
from corsarr.db import iso, now
from corsarr.llm import LLM, BudgetReached, LLMFailed
from helpers import FakeConfig as Cfg, make_bot


def test_cost_estimate_uses_cache_prices():
    tokens = {"input": 1_000_000, "cache_read": 1_000_000, "cache_write": 0, "output": 1_000_000}
    inp, out = llm.MODEL_PRICES["claude-haiku-5-5"]
    assert llm.usage_cost("claude", "claude-haiku-5-5", tokens) == pytest.approx(inp + 0.1 * inp + out)
    assert llm.usage_cost("ollama", "qwen3", tokens) == 0.0
    assert llm.usage_cost("openai", "gpt-x", tokens) is None


def test_summary_by_period_and_kind(db):
    db.add_usage("claude", "claude-haiku-5-5", "Selection", {"input": 4000, "output": 600})
    db.add_usage("claude", "claude-haiku-5-5", "text", {"input": 1000, "cache_read": 3000, "output": 100})
    db.conn.execute("UPDATE llm_usage SET ts=? WHERE kind='text'", (iso(now() - timedelta(days=40)),))
    s = usage.summary(db, Cfg(MONTHLY_BUDGET_USD="0.01"))
    assert s["total"]["calls"] == 2 and s["month"]["calls"] == 1
    assert list(s["by_kind"]) == ["Selection"] and s["month"]["cost"] > 0
    assert s["budget"] == 0.01 and s["budget_share"] == pytest.approx(0.07, abs=0.001)


def test_budget_blocks_calls_without_spending_and_usage_is_recorded_even_for_failed_answers(db):
    class Backend:
        provider, model, last_usage = "claude", "claude-haiku-5-5", None

        async def generate(self, system, user, output_format, max_tokens):
            self.last_usage = {"input": 200_000_000, "output": 0}  # $20 worth – one call over budget
            raise LLMFailed("max_tokens")

    backend = Backend()
    m = LLM(backend)
    m.on_usage = lambda kind, tokens: db.add_usage(backend.provider, backend.model, kind, tokens)
    cfg = Cfg(MONTHLY_BUDGET_USD="5")
    m.budget_reached = lambda: usage.budget_reached(db, cfg)
    with pytest.raises(LLMFailed):
        asyncio.run(m._call("hi", None))
    assert usage.month_cost(db) >= 5
    backend.generate = None  # must not be called any more
    with pytest.raises(BudgetReached):
        asyncio.run(m._call("hi", None))
    cfg.values["MONTHLY_BUDGET_USD"] = "100"  # raised in the web interface: works again right away
    assert usage.budget_reached(db, cfg) is None


def test_budget_validation():
    f = config.FIELD_BY_NAME["MONTHLY_BUDGET_USD"]
    assert config.validate(f, "5") is None and config.validate(f, "2,50") is None
    assert config.validate(f, "viel") and config.validate(f, "-1")


def test_budget_warnings_once_per_level_privately_if_set(db):
    bot = make_bot(db)
    bot.cfg = Cfg(MONTHLY_BUDGET_USD="1", ADMIN_CHAT_ID="4242")
    run = lambda: asyncio.run(bot.job_budget(None))
    run()
    assert bot.app.bot.messages == []  # nothing used yet
    db.add_usage("claude", "claude-haiku-5-5", "text", {"input": 9_000_000})  # ≈ $0.90
    run(); run()
    assert bot.app.bot.recipients == [4242]  # 80 % warning once, to the admin
    db.add_usage("claude", "claude-haiku-5-5", "text", {"input": 2_000_000})
    run(); run()
    assert len(bot.app.bot.messages) == 2 and "⛔" in bot.app.bot.messages[1][0]


def test_private_message_is_remembered_as_admin_candidate(db):
    bot = make_bot(db)
    replies = []

    class Msg:
        chat_id = 777

        async def reply_text(self, text, **kwargs):
            replies.append(text)
    user = type("U", (), {"full_name": "Sam Example", "first_name": "Sam", "username": "sam", "id": 777,
                          "language_code": "en"})()
    update = type("Up", (), {"effective_message": Msg(), "effective_user": user})()
    asyncio.run(bot.on_private(update, None))
    assert "777" in replies[0] and "admin" in replies[0].lower()
    import json
    assert json.loads(db.get_state("private_chats"))[0]["name"] == "Sam Example"


def test_small_amounts_are_not_rounded_up_to_the_budget():
    from corsarr import i18n
    with i18n.use_language("de"):
        assert usage.usd(0.016) == "0,016" and usage.usd(0.0004) == "0,0004" and usage.usd(2) == "2.00".replace(".", ",")
    with i18n.use_language("en"):
        assert usage.usd(0.016) == "0.016" and usage.usd(12.5) == "12.50"
