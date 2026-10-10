# Development

## Run and test locally

```bash
git clone https://github.com/ironiro/corsarr.git && cd corsarr
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest   # Python 3.11 or newer
.venv/bin/python -m pytest -q        # needs no credentials and no running services
.venv/bin/python -m corsarr          # web interface on http://localhost:8787/
```

Windows: `.venv\Scripts\…` instead of `.venv/bin/…`. Check connections without the web interface (posts
nothing): `python -m corsarr.check`.

Corsarr runs on Python 3.11 or newer (Debian 12 ships 3.11, the Docker image uses 3.12) – don't use syntax or
library features that need 3.12.

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and pull request: the tests on Python 3.11 and
3.12, and `pip-audit` against `requirements.txt` (the build fails on a known vulnerability – bump the package;
if there is no fixed version yet, list the advisory id in `IGNORE` in the audit job with a comment and remove it
once there is). Pushes to `main` and release tags also publish the Docker image `ghcr.io/<owner>/corsarr`
(amd64 + arm64, → [Publishing a release](#publishing-a-release)). Third-party actions are pinned to commit SHAs
with a `# vX.Y.Z` comment; Dependabot (`.github/dependabot.yml`) opens weekly pull requests for them, for the
Python dependencies and for the Docker base image.

## Updating dependencies

`requirements.in` lists the direct dependencies with the oldest versions that are known to work – edit that file.
`requirements.txt` is generated from it and pins every package, including transitive ones, so that every
installation (install.sh, the Docker image, a manual checkout) gets the same set that CI tested. Never edit it
by hand; regenerate it with [uv](https://docs.astral.sh/uv/) (`pip install uv` into any environment;
`pip-compile --no-header -o requirements.txt requirements.in` from pip-tools works too, but only resolves for the
platform it runs on):

```bash
uv pip compile requirements.in --python-version 3.11 --universal --no-header -o requirements.txt   # resolve
uv pip compile requirements.in --python-version 3.11 --universal --no-header -U -o requirements.txt   # upgrade all
```

Keep the four comment lines at the top of `requirements.txt` (they say how the file is made). Then install the
result into a fresh environment, run the tests, check it with `pip-audit -r requirements.txt --no-deps`, and
commit both files together. Dependabot's pull requests bump the pins in `requirements.txt` directly; after
merging a few of them, regenerate the file once so the `# via` annotations and transitive pins stay consistent.

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
2. Nothing else needs mapping: `i18n.normalize()` reduces any code to its primary language (`de-AT` → `de`),
   `set_language()` accepts the new code as the default (`LANGUAGE` setting), and `use_language()` /
   `switch_language()` pick it per request. Languages without a built-in dictionary keep working as before:
   `translate.py` has the model translate the fixed Telegram texts once.
3. Extend `tests/test_gui.py`: `test_every_text_exists_in_both_languages_with_same_placeholders` compares the
   `DE` and `EN` dictionaries key by key (same keys, same placeholders) and checks that every key used in the
   code exists – add the new dictionary to that comparison.

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
git tag v1.2.0 && git push origin v1.2.0                 # stable → :1.2.0, :1.2 (+ :latest, :stable, :beta – see below)
git tag v1.3.0-beta.1 && git push origin v1.3.0-beta.1   # beta → :1.3.0-beta.1 (+ :beta)
```

Tags must look exactly like `vX.Y.Z` or `vX.Y.Z-beta.N` – other tags are ignored by the update check, and the
CI builds no image for them. The moving Docker tags only ever move forward (the `version` job in `ci.yml`
compares the pushed tag with all existing ones, ordering them like `updates.py` does: a beta comes before the
release of the same number):

- `:latest` and `:stable` point to the pushed stable tag only if it is the highest stable version – a `v1.2.1`
  hotfix pushed after `v1.3.0` gets `:1.2.1` and `:1.2`, nothing else.
- `:beta` points to the pushed tag (beta or stable) only if it is the highest version of all. A stable release
  that supersedes a beta (`v1.3.0` after `v1.3.0-beta.2`) therefore moves `:beta` too; a `v1.2.1` hotfix while
  `v1.3.0-beta.2` exists leaves it alone. This matches the update check, which treats a stable release as newer
  than every beta of the same number: beta-channel users are offered `v1.3.0`, and with Docker the advised
  `docker compose pull` of `:beta` really delivers it.

If you
also create a GitHub release for the tag (on GitHub under *Releases → Draft a new release*, or
`gh release create v1.2.0 --generate-notes`), its notes are shown in the web interface before updating.
Every push to `main` builds `:edge`.

## Line endings

`.gitattributes` enforces LF so that files edited on Windows (e.g. `install.sh`) run on Linux.

## The wiki

The [wiki](https://github.com/ironiro/corsarr/wiki) is generated from `docs/` – edit the files there, not the wiki.
`docs/publish-wiki.sh` copies the pages and screenshots into the wiki repository and turns `Page.md` links into
wiki links.

## The website

`site/` is the project website on GitHub Pages (https://ironiro.github.io/corsarr/), published by
`.github/workflows/pages.yml` whenever something in `site/` changes. It is plain HTML and CSS; its images are copies
of `assets/logo.svg` and screenshots from `docs/images/` – copy them again when the screenshots change.
