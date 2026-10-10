# Changelog

## Unreleased (beta)

- **Security:** saved API keys are only sent to the saved address (typing another address in a form now needs
  the key too); the web interface only answers under known names (protection against DNS rebinding) – another
  name is allowed with one click on the status page (setting `ALLOWED_HOSTS`), webhooks work under any name; the Telegram token and API keys never appear in the log, even at DEBUG; restoring a backup
  refuses zip bombs and drops invalid settings instead of crashing at the next start; failed logins wait for
  each other; malformed requests get a 400 instead of an error.
- When two people rate at the same time, both ratings are kept; the others now get a day from the first rating.
- A ◀️/▶️ tap while someone decides on the card is no longer lost.
- A wrong model name or another unexpected error gets a reply in the chat instead of silence.
- An ignored question after an abandoned movie no longer blocks the question when you finish it later, nor the
  title in suggestions.
- A question that cannot be posted is given up after a few tries instead of costing an AI call every 10 minutes;
  it is no longer posted twice.
- A stand-in image is no longer remembered as a title's poster; an episode without numbers no longer blocks all
  download messages; "already requested" in Jellyseerr counts as requested; "Season None" is gone.
- **Safer updates (Proxmox/LXC):** the new version's Python environment is built before switching, and if the
  new version does not start, Corsarr switches back to the previous one by itself (including the database if
  needed). Updates have time limits. The update log is now in `/var/log/corsarr/update.log`, backups before an
  update in `/var/backups/corsarr/`.
- The service runs in a systemd sandbox (it can only write to its data directory); the data directory and
  `/etc/corsarr.env` are no longer readable for other users.
- Old usage records (after 13 months), download records (after 30 days) and old suggestion cards (after 90 days)
  are cleaned up daily; only the newest 3 pre-restore copies are kept. "Total" in the usage card therefore
  covers at most the last 13 months.
- Python 3.11 or newer (Debian 12) is enough; all dependencies are pinned and checked for known vulnerabilities.
- Docker: `:beta` no longer moves back to an older stable release, `:latest` only moves with the highest stable
  release; Docker logs are limited to 30 MB.
- The Corsarr logo in the web interface (header and login) in the Video store, *arr and Friendly designs; the
  Terminal design stays text only.

## v1.0.3

- The documentation is now also a [GitHub wiki](https://github.com/ironiro/corsarr/wiki); the "Documentation" link
  in the web interface's footer and in the README lead there.

## v1.0.2

- Budget messages show small amounts precisely (0,016 $ instead of 0,02 $) and the share in per cent; the
  80 % warning says clearly that everything keeps working.
- The status checks for the Jellyfin webhook plugin and Sonarr/Radarr also verify that the address entered
  there leads to this Corsarr (an old address after a move went unnoticed so far).
- A logo for Corsarr (also the web interface's favicon), a reworked README, issue and pull request templates,
  CONTRIBUTING.md and SECURITY.md.
- Collapsible blocks on the status page ("What for (this month)", "Output of the last update") stay open
  while the page refreshes itself.

## v1.0.1

- The message confirming a request from a suggestion card is no longer a reply to the card: Telegram quotes
  the card as it looks at the moment, so after paging on the quote showed a different title.

## v1.0.0 – first stable release

Everything since v0.9.0. Corsarr is a Telegram bot for movie night: suggestions from your Jellyfin library and
new titles via Jellyseerr/Seerr, feedback after watching, bundled Sonarr/Radarr download notifications and a web
interface for everything else.

### In the chat
- **Looking up a title** by name or plot description: "there's a new Marvel series with Vision now",
  "what's the film where the guy was dead all along?" – the matching title as a card.
- **Whole film series:** "get us all the Mission: Impossible films" lists every part (✅ in the library,
  ⏳ requested, 🆕 missing) with one button that requests all missing ones.
- **Where to stream it:** cards for new titles name the streaming services you subscribe to that include
  them. Set them in the web interface or in the chat ("we have Netflix and Disney+", "we cancelled Prime").
- **Ratings per person:** both of you rate after the film; suggestions respect both tastes, and what one of you
  clearly dislikes drops down.
- **Twelve characters** – pirate, Gen Z, butler, film critic, video store clerk, noir detective, trailer voice,
  ship's computer, grandma, sports commentator, cat and bard – each switched on or off on its own, in the chat
  ("behave like a grandma") or in the web interface. Pirate and Gen Z are on by default.
- **Any language:** the bot answers in the language it is written to in; fixed texts are translated once by
  the model and stored.
- **Runtime limits** ("max 2 hours") are respected.
- Missed Sonarr/Radarr webhooks (e.g. during a restart) are **caught up** from their history.

### Web interface
- **Setup assistant:** finds your Telegram group by itself, tests every connection before saving, creates the
  webhooks in Sonarr and Radarr, and can restore a backup on a fresh installation.
- **Four designs:** Video store (default), *arr, Terminal and Friendly.
- **Usage and cost:** calls, tokens and the estimated cost per day, month and in total; an optional **monthly
  budget** pauses the bot when it is used up, with a warning at 80 % – privately to the admin if set.
- **Encrypted backup and restore** (AES zip with its own password).
- **Active status checks** for the Jellyfin webhook plugin and – with remembered access – Sonarr/Radarr,
  including a test event on "Check now".
- Tidier configuration page in collapsible sections; event log that survives restarts; footer with the
  version and project links.

### AI providers
- Claude (Haiku 5.5) is recommended and tested. OpenAI, Google Gemini, Ollama and LM Studio can be selected
  but are **not tested**.

### Operations
- **Release channels:** stable, beta and development, chosen in the web interface; Docker images
  `:latest`/`:stable`, `:beta` and `:edge`.
- A backup of database and settings before every update; database and configuration carry a format version,
  and an older Corsarr refuses a database a newer one converted instead of misreading it.

### Upgrading from v0.9.0
Update from the web interface (Status → Version) or with `docker compose pull && docker compose up -d`. The
database is converted automatically (format 2: ratings per person); the installer keeps a copy from before the
update in `backups/`.

> **Cost disclaimer:** Corsarr uses paid AI services with your own API key, and you alone pay for that usage.
> No liability whatsoever is accepted for API costs. Set a spending limit with your provider.
