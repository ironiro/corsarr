# Corsarr

A Telegram bot for movie night. Corsarr suggests movies and series to your group – from your Jellyfin
library first, plus new titles via Jellyseerr. After you've watched something, it asks how you liked it and learns your taste.
It also reports what Sonarr and Radarr downloaded, grouped together rather than one message per episode. It answers in
whatever language you write in – in the voice of up to twelve characters (🏴‍☠️ pirate, 📱 Gen Z, 🎩 butler, 👵 grandma, 🐈 cat, …) or a neutral one. Powered by Claude Haiku via the Claude API, with a web interface for status, events and
configuration. OpenAI, Gemini, Ollama and LM Studio can be selected too, but are **untested**.

![Corsarr in a Telegram group: a suggestion card, feedback after the movie and download notifications](docs/images/telegram-chat.jpg)

*Illustration with made-up names.*

## Features

- **Suggestions:** `@bot find us a thriller for tonight` → 5–6 titles as **one browsable card** (◀️ ▶️):
  the first 1–2 from your library, the rest new. Each comes with a poster, a reason why it fits and a 🎬 trailer link.
  Buttons: ✅ Let's watch · 📥 Request (via Jellyseerr) · 🙅 Not interested.
- **Looking up a title:** `@bot there's a new Marvel series with Vision now` or `@bot what's the film where the
  guy was dead all along?` → the matching title as a card, ready to watch or request.
- **Based on your history:** `@bot what fits what we watched lately?`
- **Feedback per person:** both rate after the film, and suggestions respect both tastes.
- **Feedback:** movie finished, season done, series paused, movie abandoned – the bot asks and remembers
  free text like "too gory" as *less: gore*.
- **Download notifications:** new episodes one by one, backfilled seasons as a single message
  ("📦 Grey's Anatomy: 48 episodes from seasons 1–4 are ready").
- **Web interface** on port 8787: a **setup assistant** (finds your Telegram group by itself, tests every
  connection, creates the Sonarr/Radarr webhooks), connection status, live event log, configuration,
  encrypted **backup and restore**, and four designs to choose from. No config file needed.
- **Language model:** Claude (recommended and tested). OpenAI (ChatGPT), Google Gemini, Ollama and LM Studio
  are available as alternatives but **not tested** – suggestions may be worse or fail. See
  [Configuration](docs/Configuration.md#ai-provider).

![Status page of the web interface](docs/images/status.jpg)

**Costs:** Corsarr is free; Claude API usage is billed by Anthropic. One suggestion request costs about a fifth
of a cent, so $5 of credit lasts well over a year of normal use ([details](docs/Home.md#what-does-it-cost)).

> [!WARNING]
> **Cost disclaimer:** Corsarr calls paid AI services with *your own* API key, and you alone pay for that usage.
> The author accepts **no responsibility or liability whatsoever for API costs** – including unexpectedly high
> costs caused by bugs, misconfiguration, expensive models or misuse. Always set a spending limit with your
> provider. Use at your own risk.

## Quick start

**Proxmox** – in the Proxmox host shell; creates a Debian 12 container (1 core, 512 MB) and installs Corsarr in it:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/proxmox.sh)"
```

**Docker:**

```bash
curl -fsSLO https://raw.githubusercontent.com/ironiro/corsarr/main/docker-compose.yml
docker compose up -d
```

Then open `http://<ip>:8787/` – the **setup assistant** guides you through Telegram, the AI provider,
Jellyfin, Jellyseerr and the webhooks, and tests each connection before saving it ([Setup](docs/Setup.md)).

Updates come through the web interface (Status → Version) or with `docker compose pull && docker compose up -d`.
There are **stable** and **beta** releases; pick your channel under Configuration → Interface and updates
([release channels](docs/Operations.md#release-channels)).

## Documentation

| | |
| --- | --- |
| [Installation](docs/Installation.md) | LXC, Docker, manual, macOS/Windows |
| [Setup](docs/Setup.md) | The setup assistant, and where each value comes from (Telegram, AI provider, Jellyfin, Jellyseerr) |
| [Configuration](docs/Configuration.md) | All settings and the web interface |
| [Webhooks](docs/Webhooks.md) | Jellyfin (feedback), Sonarr/Radarr (download notifications) |
| [Usage](docs/Usage.md) | What to write to the bot, buttons, feedback rules |
| [Operations](docs/Operations.md) | Logs, updates and release channels, backup and restore, moving to another host |
| [Troubleshooting](docs/Troubleshooting.md) | Common problems |
| [Development](docs/Development.md) | Architecture, tests, adding a language |

## Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
.venv/bin/python -m corsarr
```

The tests need no credentials and no running services. Details: [Development](docs/Development.md).

## License

[GPL-3.0](LICENSE) – free to use, study, modify and share. If you distribute a modified version, its
source code must be available under the same licence.
