# Development

## Run and test locally

```bash
git clone https://github.com/ironiro/corsarr.git && cd corsarr
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q        # needs no credentials and no running services
.venv/bin/python -m corsarr          # web interface on http://localhost:8787/
```

Windows: `.venv\Scripts\…` instead of `.venv/bin/…`. Check connections without the web interface (posts
nothing): `python -m corsarr.check`.

GitHub Actions (`.github/workflows/ci.yml`) runs the tests on every push and pull request and publishes the
Docker image `ghcr.io/<owner>/corsarr` (amd64 + arm64) for pushes to `main` and for release tags
(→ [Publishing a release](#publishing-a-release)).

## Structure

```
Telegram ──▶ bot.py ──▶ llm.py (Claude: understand, pick, phrase)
                │
                ├──▶ recommender.py ──▶ jellyfin.py / jellyseerr.py
                │          └──▶ profile.py (taste from history + feedback)
                └──▶ db.py (SQLite)

Jellyfin ──webhook──▶ web.py ──▶ feedback.py ──▶ bot.py (feedback question)
Sonarr/Radarr ──────▶ web.py ──▶ arr.py ──(job, bundled)──▶ bot.py (download notification)
Browser ────────────▶ web.py ──▶ config.py / monitor.py / checks.py / setup.py / backup.py / updates.py
```

| Module | Purpose |
| --- | --- |
| `main.py` | Startup; `Runtime` holds configuration, database and the restartable Telegram part. The web server always runs. |
| `bot.py` | Telegram: mentions, browsable suggestion card, buttons, feedback questions, outage mode, jobs |
| `recommender.py` | Collect candidates (max. 2 from the library + new ones), sort by genre matches and profile, let the model pick; title lookup and film series |
| `profile.py` | Taste profile: genre weights from history/favourites, feedback, traits – shared and per person; title lists for the model |
| `feedback.py` | Webhook processing (movie end, season end, pause, abort), deadlines, storing ratings |
| `arr.py` | Store Sonarr/Radarr imports, catch up missed ones from their history, bundle them into messages |
| `llm.py` | Language model calls with structured output, repetition guard; Claude via the Anthropic SDK (with prompt caching), the untested providers via the OpenAI-compatible chat API (httpx) |
| `persona.py` | The twelve characters: which one speaks, dialogs, post-processing (emoji per line) |
| `translate.py` | Fixed Telegram texts for languages other than German/English, translated once by the model and stored |
| `usage.py` | Token usage, estimated cost, monthly budget |
| `jellyfin.py`, `jellyseerr.py` | API clients |
| `db.py` | SQLite schema, migrations, queries |
| `web.py` + `web/` | Web interface (HTML/JS/CSS without a build step; the four designs are CSS scoped by `data-skin`), JSON API, webhook endpoints |
| `config.py` | Fields, sources (GUI > environment > `.env` > default), validation |
| `monitor.py` | Connection status and event buffer for the web interface |
| `checks.py` / `check.py` | Active connection checks (web interface and command line) |
| `setup.py` | Setup assistant: tests unsaved values, finds the Telegram group, creates the Sonarr/Radarr webhooks |
| `backup.py` | Encrypted backup zip (AES) and restore |
| `updates.py` | Version check against GitHub per release channel, updates started from the web interface |
| `i18n.py` | All texts in German and English; language per request (`use_language`, any language), logs always English |
| `poster.py` | Placeholder poster for titles without an image |

## Principles

- **Facts come from the code**, never from the model: titles, plots, ratings, availability. Claude picks and
  phrases. Whatever must always be right (title line of feedback questions, card header, buttons) is set by
  the code itself.
- **Keep costs low:** status checks are free (model lookup only), the stable part of the prompt is cached,
  download notifications don't need Claude at all.
- **Nothing gets lost:** webhooks are stored first, missed jobs run later.

## Adding a language

1. In `corsarr/i18n.py` add another dictionary like `EN` (all keys, same placeholders) and register it in
   `TEXTS` and `LANGUAGES`.
2. Map the language code in `supported()` – currently everything except German maps to English.
3. `tests/test_gui.py` automatically checks that every language has all keys with the same placeholders and
   that every key used in the code exists.

Prompts, character texts, bot messages and web interface texts all live in that file. Claude detects a message's language
while understanding it (`Understanding.language`); `CorsarrBot._adopt_language` switches to it and remembers
it for messages the bot sends on its own. Log lines (`log.*`) are always English.

## Changing the database or the configuration

New tables go into `SCHEMA` (`db.py`) with `CREATE TABLE IF NOT EXISTS`. New columns on existing tables must
also be added to `DB._migrate()` – otherwise existing installations won't have them. **Raise `SCHEMA_VERSION`**
with every such change: an older Corsarr then refuses the converted database instead of misreading it.

Renaming a configuration field or changing what a value means: raise `CONFIG_VERSION` in `config.py` and
convert older files in `_migrate_overrides()`. New fields need nothing – a missing value means the default.

## Publishing a release

`main` is the development branch. A release is a git tag; the CI builds the Docker images for it.

```bash
git tag v1.2.0 && git push origin v1.2.0                 # stable → :latest, :stable, :beta, :1.2.0, :1.2
git tag v1.3.0-beta.1 && git push origin v1.3.0-beta.1   # beta → :beta, :1.3.0-beta.1
```

Tags must look exactly like `vX.Y.Z` or `vX.Y.Z-beta.N` – other tags are ignored by the update check. If you
also create a GitHub release for the tag (on GitHub under *Releases → Draft a new release*, or
`gh release create v1.2.0 --generate-notes`), its notes are shown in the web interface before updating.
Every push to `main` builds `:edge`.

## Line endings

`.gitattributes` enforces LF so that files edited on Windows (e.g. `install.sh`) run on Linux.
