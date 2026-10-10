"""Data retention: once a day, drop what nothing reads any more, so the database stays small.

What goes and why it is safe (the windows are well above what the code reads back):
- llm_usage rows older than LLM_USAGE_MONTHS: the GUI shows today and the current month, the budget
  works on the current month. The all-time figures in the GUI then cover the last 13 months.
- arr_imports that were notified and are older than IMPORTS_DAYS: catch_up() only de-duplicates within
  arr.KNOWN_FOR (14 days) and looks back 48 hours at most.
- carousels, their suggestions and collections older than CARDS_DAYS: paging on such an old message just
  stops working. Suggestions that were requested stay (db.was_requested marks the download message "via
  Corsarr"), only their card JSON goes. recently_suggested (3 days) and accepted_keys (14 days) look back
  far less. Feedback requests are not touched – pause_asked reads them without a time limit.
- translations whose key no longer exists (texts removed in an update).
- the pre-restore copies backup.restore() keeps: all but the newest KEEP_RESTORES.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from . import backup, i18n
from .db import DB, now

LLM_USAGE_MONTHS = 13
IMPORTS_DAYS = 30
CARDS_DAYS = 90
KEEP_RESTORES = 3


def run(db: DB, data_dir: Path) -> dict[str, int]:
    """Apply every rule; returns how many rows/directories went per kind."""
    today = now()
    removed = {
        "usage": db.prune_usage(today - timedelta(days=LLM_USAGE_MONTHS * 31)),
        "imports": db.prune_imports(today - timedelta(days=IMPORTS_DAYS)),
        **db.prune_cards(today - timedelta(days=CARDS_DAYS)),
        "translations": db.prune_translations(set(i18n.translatable())),
        "restores": backup.prune_restores(data_dir, KEEP_RESTORES),
    }
    db.optimize()
    return removed
