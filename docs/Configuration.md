# Configuration

## Where values come from

Each value can come from one of three sources. If it is set in more than one, the first in this list wins:

1. **Web interface** – stored in `config.json` in the data directory
2. **Environment variables** – e.g. `/etc/corsarr.env` (loaded by the systemd service if present) or
   `environment:` / `env_file:` with Docker
3. **`.env`** in the start directory, or the file `CORSARR_ENV_FILE` points to

Normally you only need the web interface. Files are useful for automated setups. The web
interface marks values from the environment or `.env` with *ENV*; *↺ default* discards the value entered in
the web interface and uses the one from the environment or file again. Template with all names:
[`.env.example`](https://github.com/ironiro/corsarr/blob/main/.env.example).

If a required value is missing, only the web interface runs and shows what is missing under Status. The
Telegram part starts as soon as everything is there.

## All settings

The groups below match the sections of the Configuration page.

### Telegram

| Name | Required | Meaning |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | yes | Token from @BotFather. Never shown in the web interface. |
| `TELEGRAM_CHAT_ID` | yes | Id of the group (negative, often starting with `-100`). The bot only responds in this group. |
| `NOTIFY_CHAT_ID` | – | Separate chat for Sonarr/Radarr download notifications. If empty, the main group is used. The bot must be a member there. |

### AI provider

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

### Jellyseerr / Seerr

Jellyseerr has been merged into Seerr; Seerr and Overseerr use the same API and are entered here too.

| Name | Required | Meaning |
| --- | --- | --- |
| `JELLYSEERR_URL` | yes | e.g. `http://192.168.1.21:5055` |
| `JELLYSEERR_API_KEY` | yes | Settings → General → API Key |
| `STREAMING_PROVIDERS` | – | Streaming services you subscribe to, as comma-separated TMDB provider ids (e.g. `8,337` for Netflix and Disney+). In the web interface a checklist of the services in your country. Cards for new titles then show which of them include the title ([Usage](Usage.md#the-suggestion-card)). Empty = off. Applies immediately. |
| `STREAMING_REGION` | – | Country whose streaming catalogues count, as a two-letter code (`DE`, `US`, …). Empty = derived from `LANGUAGE` (`de` → `DE`, otherwise `US`). Applies immediately. |

### Webhooks

| Name | Required | Meaning |
| --- | --- | --- |
| `WEBHOOK_SECRET` | yes* | Shared secret for Jellyfin, Sonarr and Radarr. *Generated automatically on first start. The ready-made webhook addresses are under Setup → Webhooks. |
| `SONARR_URL`, `SONARR_API_KEY` | – | Optional. Only for the active [status check](Webhooks.md#status-checks) of Sonarr; set by *Remember access* in the setup assistant. |
| `RADARR_URL`, `RADARR_API_KEY` | – | The same for Radarr. |

### Interface and updates

| Name | Required | Meaning |
| --- | --- | --- |
| `LANGUAGE` | – | `en` (default) or `de` – language of the web interface, and of the bot until someone writes to it. After that the bot answers in the language of each message. The log is always English. |
| `ADMIN_PASSWORD` | – | Password for the web interface. Empty = no login. Required for downloading backups. |
| `UPDATE_CHANNEL` | – | `stable` (default), `beta` or `dev` ([release channels](Operations.md#release-channels)). Applies immediately. |

### Advanced

| Name | Required | Meaning |
| --- | --- | --- |
| `WEBHOOK_HOST` | – | Address the web server listens on. Default `0.0.0.0` (all). Takes effect after restarting the program. |
| `WEBHOOK_PORT` | – | Port of the web interface and the webhooks. Default `8787`. Takes effect after restarting the program. |
| `LOG_LEVEL` | – | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` |
| `DATA_DIR` | – | Data directory. **Only** via environment/`.env`, not in the web interface. LXC: `/var/lib/corsarr`, Docker: `/data`, run manually: `./data` |

Saving changes in the web interface restarts the Telegram part within the running program – no systemd or container
restart needed. Exception: `WEBHOOK_HOST`/`WEBHOOK_PORT` (the web server itself) – restart the whole program
for those (`systemctl restart corsarr` or `docker compose restart`).

## Bot behaviour

These settings live in the database, apply immediately and can also be changed in the chat.

| Setting | Default | In the chat, e.g. |
| --- | --- | --- |
| Series pause: ask after … days (1–365) | 14 | `@bot ask about series only after 3 weeks` |
| Abandoned movies: ask after … days (1–60) | 3 | `@bot ask about abandoned movies after 5 days` |
| Characters (12 switches) | Pirate and Gen Z on, the others off | `@bot no more pirate`, `@bot switch grandma on` |

The characters are listed in [Usage](Usage.md#characters-and-language). With all of them off, the bot writes in a
plain, neutral style.

## The web interface

`http://<bot-ip>:8787/` – five tabs: Status, Events, Configuration, Setup and Backup.

Pick a look with the **Design** menu: *Video store* (the default), *\*arr*, *Terminal* or *Friendly*. The
choice is remembered in your browser.

![Status: the bot and every connection at a glance](images/status.jpg)

- **Status:** state of the bot and of every connection (Telegram, the AI provider, Jellyfin, Jellyseerr, Jellyfin
  webhook, Sonarr, Radarr) – OK/error with a short detail and time. Checked every 5 minutes (for Claude, only
  the key and model, which is free) and also whenever the bot actually uses a service. Buttons: *Check now*,
  *Restart bot*. Below the bot: **Version** – installed vs. newest version in your channel, the changes in
  between and, for LXC installations, an *Update now* button (→ [Operations](Operations.md#update)).

![Events: what the bot understood, suggested and reported](images/events.jpg)

- **Events:** the last 1000 log entries since start, live, with filter and search. Among other things it
  shows every message to the bot and how it understood it, every suggestion with its reason and every
  notification.

![Configuration: bot behaviour and the collapsible connection sections](images/configuration.jpg)

- **Configuration:** *Bot behaviour* at the top saves each change immediately ("✓ saved" next to it). Below
  it, the settings above in collapsible sections – Telegram, AI provider, Jellyfin, Jellyseerr / Seerr,
  Webhooks, Interface and updates, Advanced. A closed section shows a one-line summary and whether a required
  field is missing; sections with missing fields open by themselves. Technical names (the variables in the
  tables above) appear when you hover over a field. Values that come from the environment are marked *ENV*;
  *↺ default* discards the value entered here. Everything is saved together with **Save**, which is only
  active when something changed. API keys are never shown – an empty field means "unchanged". The Jellyfin
  account and the model are picked from lists that Corsarr loads from Jellyfin and from the AI provider; for
  Claude the model list shows what each model costs compared with the recommended Claude Haiku and warns
  before switching to an expensive one. Selecting a provider other than Claude shows an "untested" warning
  and asks for confirmation before saving.
- **Setup:** the setup assistant (→ [Setup](Setup.md#the-setup-assistant)), including the webhook addresses
  and the one-click webhook setup for Sonarr and Radarr.
- **Backup:** download an encrypted backup and restore one (→ [Operations](Operations.md#backup)).

Without `ADMIN_PASSWORD` anyone on your home network can open the web interface and change settings, so
**don't forward port 8787 to the internet**. Other websites still cannot trigger anything through your
browser (cross-site request protection is always on).

If you forget the password: delete the `ADMIN_PASSWORD` entry from `config.json` in the data directory and restart
the bot.
