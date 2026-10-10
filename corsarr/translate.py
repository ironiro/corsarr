"""Fixed Telegram texts in languages without built-in texts, translated once by the model and stored.

German and English are built in (i18n.py). When the group writes in another language, ensure() asks the
model to translate the English bot.*/notify.* texts (buttons, card lines, download messages) – one call of
a few thousand tokens per language – checks that placeholders and HTML tags survived, and stores the result.
Texts whose English source changed in an update are translated again; until then English is used.
"""
from __future__ import annotations

import logging
import time

from . import i18n
from .db import DB
from .i18n import t
from .llm import LLM, LLMFailed, LLMUnavailable

log = logging.getLogger(__name__)

CHUNK = 60          # texts per model call
RETRY_AFTER = 3600  # seconds before a failed language is tried again


def valid(source: str, text: str) -> bool:
    """A translation must keep the placeholders and the HTML tags of its source."""
    return bool(text.strip()) and i18n.placeholders(source) == i18n.placeholders(text) and all(
        source.count(tag) == text.count(tag) for tag in ("<b>", "</b>", "<i>", "</i>", "\n"))


class Translations:
    def __init__(self, db: DB, llm: LLM):
        self.db, self.llm = db, llm
        self._failed: dict[str, float] = {}
        self.load()

    def load(self) -> None:
        """Stored translations whose English source is unchanged."""
        sources = i18n.translatable()
        by_lang: dict[str, dict[str, str]] = {}
        for lang, key, source, text in self.db.translations():
            if sources.get(key) == source and valid(source, text):
                by_lang.setdefault(lang, {})[key] = text
        for lang, texts in by_lang.items():
            i18n.add_translations(lang, texts)

    def missing(self, lang: str) -> dict[str, str]:
        have = i18n._translated.get(lang, {})
        return {k: v for k, v in i18n.translatable().items() if k not in have}

    async def ensure(self, lang: str) -> None:
        """Make sure the fixed texts exist in `lang`; on failure English is used and it is retried later."""
        if i18n.has_builtin(lang) or time.monotonic() - self._failed.get(lang, -RETRY_AFTER) < RETRY_AFTER:
            return
        missing = self.missing(lang)
        if not missing:
            return
        keys = list(missing)
        done = 0
        try:
            for i in range(0, len(keys), CHUNK):
                chunk = {k: missing[k] for k in keys[i:i + CHUNK]}
                got = await self.llm.translate(lang, chunk)
                ok = {k: v for k, v in got.items() if k in chunk and valid(chunk[k], v)}
                self.db.save_translations(lang, {k: (chunk[k], v) for k, v in ok.items()})
                i18n.add_translations(lang, ok)
                done += len(ok)
        except (LLMUnavailable, LLMFailed) as e:
            self._failed[lang] = time.monotonic()
            log.warning(t("log.translate_failed", lang=lang, error=e))
        if done:
            log.info(t("log.translated", lang=lang, n=done, total=len(keys)))
        if done < len(keys):
            self._failed.setdefault(lang, time.monotonic())  # some were rejected – don't ask again right away
