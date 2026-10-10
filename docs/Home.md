# Corsarr – Wiki

Corsarr helps a small Telegram group pick movies and series for the evening. It knows your Jellyfin
library, suggests new titles via Jellyseerr when needed, asks how you liked something after watching it and learns your
taste from that. It also reports when Sonarr or Radarr has finished a download, grouping episodes together instead of sending one message per
episode.

## What it does

- **Suggestions on request:** `@bot find a thriller for tonight` → 5–6 titles as one browsable card, the
  first 1–2 from your library, the rest new and requestable. With poster, rating, runtime, a reason and a
  trailer link.
- **Suggestions from your history:** `@bot what fits what we watched lately?`
- **Feedback after watching:** movie finished, season done, series paused, movie abandoned – the bot asks
  and remembers things like *less: gore*.
- **Download notifications** from Sonarr and Radarr: new episodes one by one, backfilled seasons as one
  message.
- **Web interface** with a setup assistant, connection status, event log, the complete configuration and
  encrypted backup and restore.
- **Twelve characters** – pirate, Gen Z, butler, film critic, video store clerk, noir detective, trailer voice,
  ship's computer, grandma, sports commentator, cat and bard – each switched on or off on its own. Answers in whatever language you write in
  (web interface in English and German).

## Pages

| Page | Contents |
| --- | --- |
| [Installation](Installation.md) | Proxmox LXC (one command), Docker, manual, macOS/Windows |
| [Setup](Setup.md) | The setup assistant, and where the bot token and keys come from |
| [Configuration](Configuration.md) | All settings, where they come from, the web interface in detail |
| [Webhooks](Webhooks.md) | Jellyfin (feedback), Sonarr and Radarr (download notifications) |
| [Usage](Usage.md) | What to write to the bot, buttons, feedback rules |
| [Operations](Operations.md) | Updates and release channels, logs, backup and restore, moving |
| [Troubleshooting](Troubleshooting.md) | Common problems and how to fix them |
| [Development](Development.md) | Code structure, tests, adding a language |

## Requirements

- A machine that is always on: Proxmox LXC, Docker host, Raspberry Pi or similar (512 MB RAM is enough)
- Jellyfin with a shared account
- Jellyseerr (for new titles and requests). Seerr, which Jellyseerr has been merged into, uses the same API,
  as does Overseerr – both work the same way.
- A Telegram bot (free via @BotFather)
- A Claude API key with a few dollars of prepaid credit (→ [Setup](Setup.md#2-claude-api-key), and see the
  costs below). OpenAI, Gemini, Ollama and LM Studio can be selected instead, but are **untested**.
- Optional: Sonarr and Radarr

## What does it cost?

Corsarr itself is free. You only pay Anthropic for the Claude API, and with Claude Haiku that is very little.
Measured on real use (prices as of October 2026: $0.10 per million input tokens, $0.50 per million output
tokens):

| What happens | Cost |
| --- | --- |
| One suggestion request ("find us a thriller") | about **$0.002** (a fifth of a cent) |
| A feedback question, or a short reply from the bot | about $0.0005 |
| Status checks, download notifications, browsing the card | free – they don't use Claude |

So **$5 of credit is roughly 2,500 suggestion requests.** Typical use – a few requests and a feedback
question per evening – comes to around 10–20 cents a month, and $5 lasts well over a year. Even heavy use
(ten requests every day) stays under $1 a month.

The status page shows the estimated cost of your own use, and an optional monthly budget pauses the bot
when it is used up ([details](Operations.md#keeping-an-eye-on-costs)). Prices can change; the [Claude Console](https://console.anthropic.com/) shows what you actually spent under
*Usage*. Set a monthly spend limit there so there are never surprises.

These figures apply to Claude Haiku. Other Claude models cost 20–100 times as much. The untested alternative
providers (OpenAI, Gemini) have their own prices; local models via Ollama or LM Studio cost nothing per request.

> [!WARNING]
> **Cost disclaimer:** Corsarr calls paid AI services with *your own* API key, and you alone pay for that
> usage. The author accepts **no responsibility or liability whatsoever for API costs** – including
> unexpectedly high costs caused by bugs, misconfiguration, expensive models or misuse. Always set a spending
> limit with your provider. Use at your own risk.
