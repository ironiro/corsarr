# Configuration

## Where values come from

Each value can come from one of three sources. If it is set in more than one, the first in this list wins:

1. **Web interface** – stored in `config.json` in the data directory
2. **Environment variables** – e.g. `/etc/corsarr.env` (loaded by the systemd service if present) or
   `environment:` / `env_file:` with Docker
3. **`.env`** in the start directory, or the file `CORSARR_ENV_FILE` points to

Normally you only need the web interface. Files are useful for automated setups. The web
interface shows where each value currently comes from; **reset** discards the GUI value and uses the
one from the environment or file again. Template with all names:
[`.env.example`](https://github.com/ironiro/corsarr/blob/main/.env.example).

If a required value is missing, only the web interface runs and shows what is missing under Status. The
Telegram part starts as soon as everything is there.

## All settings

### Telegram

| Name | Required | Meaning |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | Token from @BotFather. Never shown in the web interface. |
| `TELEGRAM_CHAT_ID` | yes | Id of the group (negative, often starting with `-100`). The bot only responds in this group. |
| `NOTIFY_CHAT_ID` | – | Separate chat for Sonarr/Radarr download notifications. If empty, the main group is used. The bot must be a member there. |

### Language model (AI provider)

| Name | Required | Meaning |
| --- | --- | --- |
| `LLM_PROVIDER` | – | `claude` (default, **recommended and tested**), `openai`, `gemini`, `ollama` or `lmstudio` (all **untested**). |
| `ANTHROPIC_API_KEY` | with `claude` | API key from the Claude Console. |
| `CLAUDE_MODEL` | – | Default `claude-haiku-5-5` – recommended. Larger models cost 20–100 times as much per suggestion ([costs](Home.md#what-does-it-cost)) and are rarely noticeably better for picking movies. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | with `openai` | Key from platform.openai.com and a chat model id. |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | with `gemini` | Key from Google AI Studio and a Gemini model id. |
| `OLLAMA_URL`, `OLLAMA_MODEL` | with `ollama` | Default `http://localhost:11434`; the model must already be pulled (`ollama pull …`). |
| `LMSTUDIO_URL`, `LMSTUDIO_MODEL` | with `lmstudio` | Default `http://localhost:1234`; start LM Studio's local server first. |

Only the fields of the selected provider are shown and used. In the web interface the model is picked from a
list that Corsarr loads from the provider.

> [!WARNING]
> **Only Claude is tested.** Corsarr is developed and tested with Claude Haiku. The other providers are
> connected through their OpenAI-compatible API and were not tested with real use. Suggestions may be worse,
> answers may fail ("That didn't work") or individual features may not work. If you run into problems, switch
> back to Claude.

Notes for the untested providers:

- **Ollama / LM Studio** run on your own hardware and cost nothing per request. The address must be
  reachable from where Corsarr runs: inside Docker or an LXC container, `localhost` is the container itself,
  so use the IP of the machine running the model. Ollama only listens on localhost by default – set
  `OLLAMA_HOST=0.0.0.0` on that machine to reach it from the network.
- Corsarr needs answers in a fixed JSON structure. **Small local models (a few billion parameters) often get
  that wrong**, or pick genres poorly. Larger models work better but need a capable GPU and answer slowly.
- **OpenAI and Gemini** are paid per use like Claude. Corsarr shows no cost estimate for them – check
  the provider's price list and set a spending limit there.

### Jellyfin

| Name | Required | Meaning |
| --- | --- | --- |
| `JELLYFIN_URL` | yes | e.g. `http://192.168.1.20:8096` |
| `JELLYFIN_API_KEY` | yes | Dashboard → API Keys |
| `JELLYFIN_USER` | yes | Name or id of the shared account |

### Jellyseerr

| Name | Required | Meaning |
| --- | --- | --- |
| `JELLYSEERR_URL` | yes | e.g. `http://192.168.1.21:5055` |
| `JELLYSEERR_API_KEY` | yes | Settings → General → API Key |

### Web server and webhooks

| Name | Required | Meaning |
| --- | --- | --- |
| `WEBHOOK_HOST` | – | Address the web server listens on. Default `0.0.0.0` (all). Takes effect after restarting the program. |
| `WEBHOOK_PORT` | – | Default `8787`. Takes effect after restarting the program. |
| `WEBHOOK_SECRET` | yes* | Shared secret for Jellyfin, Sonarr and Radarr. *Generated automatically on first start and shown in the web interface. |
| `ADMIN_PASSWORD` | – | Password for the web interface. Empty = no login. |

### System

| Name | Meaning |
| --- | --- |
| `LANGUAGE` | `en` (default) or `de` – language of the web interface, and of the bot until someone writes to it. After that the bot answers in the language of each message. The log is always English. |
| `LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |
| `DATA_DIR` | Data directory. **Only** via environment/`.env`, not in the web interface. LXC: `/var/lib/corsarr`, Docker: `/data`, run manually: `./data` |

Saving changes in the web interface restarts the Telegram part within the running program – no systemd or container
restart needed. Exception: `WEBHOOK_HOST`/`WEBHOOK_PORT` (the web server itself) – restart the whole program
for those (`systemctl restart corsarr` or `docker compose restart`).

## Bot behaviour

These settings live in the database, apply immediately and can also be changed in the chat.

| Setting | Default | In the chat, e.g. |
| --- | --- | --- |
| Series pause: ask after … days (1–365) | 14 | `@bot ask about series only after 3 weeks` |
| Abandoned movies: ask after … days (1–60) | 3 | `@bot ask about abandoned movies after 5 days` |
| 🏴‍☠️ Pirate active | on | `@bot no more pirate` |
| 📱 Gen Z active | on | `@bot Gen Z back on` |

With both characters off, the bot writes in a plain, neutral style.

## The web interface

Pick one of four looks with the **Design** menu (*arr*, *Terminal*, *Video store*, *Friendly*); the choice is
remembered in your browser. Besides Status, Events and Configuration there are **Setup** (the setup assistant,
including the webhooks for Jellyfin, Sonarr and Radarr) and **Backup** (download and restore).

`http://<bot-ip>:8787/`

![Status: the bot and every connection at a glance](images/status.jpg)

- **Status:** state of the bot and of every connection (Telegram, the AI provider, Jellyfin, Jellyseerr, Jellyfin
  webhook, Sonarr, Radarr) – OK/error with message and time. Checked every 5 minutes (for Claude, only the key
  and model, which is free) and also whenever the bot actually uses a service. Buttons: *Check now*,
  *Restart bot*. Below the bot: **Version** – installed vs. newest version, the changes in between and,
  for LXC installations, an *Update now* button (→ [Operations](Operations.md#update)).
![Events: what the bot understood, suggested and reported](images/events.jpg)

- **Events:** the last 1000 log entries since start, live, with filter and search. Among other things it
  shows every message to the bot and how it understood it, every suggestion with its reason and every
  notification.
![Configuration: bot behaviour and all connection settings](images/configuration.jpg)

- **Configuration:** everything above. *Bot behaviour* saves each change immediately ("✓ saved" next to it);
  the connection settings below are saved together with **Save**, which is only active when something
  changed. API keys are never shown – an empty field means "unchanged". The Jellyfin account and the model
  are picked from lists that Corsarr loads from Jellyfin and from the AI provider; for Claude the model list
  shows what each model costs compared with the recommended Claude Haiku and warns before switching to an
  expensive one. Selecting a provider other than Claude shows an "untested" warning and asks for
  confirmation before saving.

Without `ADMIN_PASSWORD` anyone on your home network can open the web interface and change settings, so
**don't forward port 8787 to the internet**. Other websites still cannot trigger anything through your
browser (cross-site request protection is always on).

If you forget the password: delete the `ADMIN_PASSWORD` entry from `config.json` in the data directory and restart
the bot.
