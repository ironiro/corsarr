"""What the language model costs: token counts per call (recorded by llm.LLM), estimated $ and the budget.

Prices are Anthropic's list prices (llm.MODEL_PRICES) – an estimate, the Claude Console shows the real bill.
Local models (Ollama, LM Studio) cost nothing; for OpenAI and Gemini Corsarr knows no prices, so only tokens
are counted. The optional monthly budget (MONTHLY_BUDGET_USD) works on the estimate of the current UTC month.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import llm
from .config import Config
from .db import DB, now
from .i18n import t

WARN_SHARE = 0.8  # warn once at 80 % of the budget


def month_start(at: datetime | None = None) -> datetime:
    at = (at or now()).astimezone(timezone.utc)
    return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _period(rows: list[dict]) -> dict:
    calls = sum(r["calls"] for r in rows)
    tokens_in = sum(r["input"] + r["cache_read"] + r["cache_write"] for r in rows)
    tokens_out = sum(r["output"] for r in rows)
    costs = [llm.usage_cost(r["provider"], r["model"], r) for r in rows]
    known = [c for c in costs if c is not None]
    return {"calls": calls, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost": round(sum(known), 4) if known or not rows else None,
            "partial": any(c is None for c in costs) and bool(known)}  # some calls had no known price


def summary(db: DB, cfg: Config) -> dict:
    """Today, this month and all time, the month's split by kind of call, and the budget."""
    today = now().astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    month = db.usage_since(month_start())
    by_kind: dict[str, dict] = {}
    for r in month:
        k = by_kind.setdefault(r["kind"], {"calls": 0, "cost": 0.0})
        k["calls"] += r["calls"]
        cost = llm.usage_cost(r["provider"], r["model"], r)
        k["cost"] = None if cost is None or k["cost"] is None else round(k["cost"] + cost, 4)
    budget = budget_usd(cfg)
    m = _period(month)
    return {"today": _period(db.usage_since(today)), "month": m, "total": _period(db.usage_since(None)),
            "by_kind": by_kind, "budget": budget,
            "budget_share": round(m["cost"] / budget, 3) if budget and m["cost"] is not None else None,
            "provider": cfg.llm_provider}


def budget_usd(cfg: Config) -> float | None:
    try:
        value = float(cfg.get("MONTHLY_BUDGET_USD").replace(",", "."))
    except ValueError:
        return None
    return value if value > 0 else None


def month_cost(db: DB) -> float | None:
    return _period(db.usage_since(month_start()))["cost"]


def budget_reached(db: DB, cfg: Config) -> str | None:
    """Reason text when this month's estimated cost reached the budget, else None."""
    budget = budget_usd(cfg)
    cost = month_cost(db) if budget else None
    if budget and cost is not None and cost >= budget:
        return t("check.budget_reached", cost=f"{cost:.2f}", budget=f"{budget:.2f}")
    return None


def next_month(at: datetime | None = None) -> datetime:
    start = month_start(at)
    return (start + timedelta(days=32)).replace(day=1)
