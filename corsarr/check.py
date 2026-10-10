"""Connectivity check: python -m corsarr.check

Tests Jellyfin, Jellyseerr, the language model and Telegram with the configured environment, without
posting anything to the group.
"""
from __future__ import annotations

import asyncio

from . import config
from .config import PROVIDER_NAMES
from .checks import run_checks


def main() -> None:
    cfg = config.load()
    for err in cfg.errors.values():
        print(f"⚠️  {err}")
    labels = {"jellyfin": "Jellyfin", "jellyseerr": "Jellyseerr", "telegram": "Telegram",
              "llm": PROVIDER_NAMES.get(cfg.llm_provider, cfg.llm_provider)}
    results = asyncio.run(run_checks(cfg, ping=True, send_tests=True))
    for name, (ok, detail) in results.items():
        print(f"{'✅' if ok else '❌'} {labels[name]}: {detail}")
    raise SystemExit(0 if all(ok for ok, _ in results.values()) and cfg.complete else 1)


if __name__ == "__main__":
    main()
