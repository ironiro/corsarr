"""Connectivity check: python -m corsarr.check

Tests Jellyfin, Jellyseerr, Claude and Telegram with the configured environment, without
posting anything to the group.
"""
from __future__ import annotations

import asyncio

from . import config
from .checks import run_checks

LABELS = {"jellyfin": "Jellyfin", "jellyseerr": "Jellyseerr", "claude": "Claude API", "telegram": "Telegram"}


def main() -> None:
    cfg = config.load()
    for err in cfg.errors.values():
        print(f"⚠️  {err}")
    results = asyncio.run(run_checks(cfg, ping=True))
    for name, (ok, detail) in results.items():
        print(f"{'✅' if ok else '❌'} {LABELS[name]}: {detail}")
    raise SystemExit(0 if all(ok for ok, _ in results.values()) and cfg.complete else 1)


if __name__ == "__main__":
    main()
