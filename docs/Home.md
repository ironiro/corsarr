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
- **Web interface** with connection status, event log and the complete configuration.
- **Two characters:** 🏴‍☠️ pirate and 📱 Gen Z (each can be switched off). Replies in English or German – whichever
  language you write in.

## Pages

| Page | Contents |
| --- | --- |
| [Installation](Installation.md) | Proxmox LXC (one command), Docker, manual, macOS/Windows |
| [Setup](Setup.md) | Create the Telegram bot, get the keys, enter everything in the web interface |
| [Configuration](Configuration.md) | All settings, where they come from, the web interface in detail |
| [Webhooks](Webhooks.md) | Jellyfin (feedback), Sonarr and Radarr (download notifications) |
| [Usage](Usage.md) | What to write to the bot, buttons, feedback rules |
| [Operations](Operations.md) | Updates, logs, backups, moving |
| [Troubleshooting](Troubleshooting.md) | Common problems and how to fix them |
| [Development](Development.md) | Code structure, tests, adding a language |

## Requirements

- A machine that is always on: Proxmox LXC, Docker host, Raspberry Pi or similar (512 MB RAM is enough)
- Jellyfin with a shared account
- Jellyseerr (for new titles and requests)
- A Telegram bot (free via @BotFather)
- A Claude API key – set a monthly spend limit in the Claude Console (e.g. $2); your actual costs are shown
  in the Console under *Usage*
- Optional: Sonarr and Radarr
