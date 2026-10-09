# Setup

After [installation](Installation.md), open the web interface at `http://<bot-ip>:8787/`. On a new
installation it starts with the **setup assistant**, which guides you through everything below and tests
each connection before saving it. The rest of this page explains where each value comes from, for reference
or if you prefer to enter things by hand.

![The setup assistant's webhooks step: ready-made addresses and one-click setup for Sonarr and Radarr](images/setup.jpg)

## The setup assistant

The assistant is the **Setup** tab. It starts by itself when nothing is configured yet, and you can run it
again at any time – for example after deleting a webhook or changing your AI provider.

First choose **Set up from scratch** or **Restore a backup**. Restoring takes a backup zip made with Corsarr
and its password, and needs no login on a fresh installation ([Backup](Operations.md#backup)).

Setting up from scratch has five steps. Each one is tested before it is saved; you can **skip** a step and
come back to it later.

1. **Telegram:** paste the bot token from @BotFather (→ [below](#1-create-the-telegram-bot)) and click
   *Check bot and find group*. Add the bot to your group and write any message there, then click
   *Search again* – the assistant finds the group by itself, so you don't need to look up a chat id. If the
   bot's privacy mode is still on, the assistant warns you.
2. **AI provider:** Claude is recommended and tested; paste your API key (→ [below](#2-claude-api-key)) and
   pick the model. OpenAI, Gemini, Ollama and LM Studio are offered too, but are **untested**.
3. **Jellyfin:** address and API key, then *Load accounts* and pick the shared account from the list.
4. **Jellyseerr / Seerr:** address and API key.
5. **Webhooks:** the ready-made addresses for Jellyfin, Sonarr and Radarr, each with a *Copy* button.
   - **Jellyfin:** copy the URL, header, secret and template into the Webhook plugin
     ([details](Webhooks.md#jellyfin--feedback-after-watching)).
   - **Sonarr / Radarr:** enter the service's address and API key (*Settings → General*) and click
     *Set up in Sonarr* / *Set up in Radarr*. Corsarr creates a webhook named `Corsarr` there (trigger
     *On File Import* only), or updates it if it already exists. The address and key are only used for this
     and are **not saved**. If the service still has its own Telegram connection, the assistant warns you –
     remove it, or every message arrives twice.
   - Next to each service the assistant shows *waiting for the first event* until the first event arrives,
     then *received*.

   Open the page via the bot's IP rather than `localhost`, otherwise the addresses shown won't work from the
   other machines (the assistant points this out).

Then try it in the group: `@yourbot find a movie for tonight`.

## 1. Create the Telegram bot

1. In Telegram open **@BotFather** and send `/newbot`. Choose a name and a username (ending in `bot`).
   BotFather replies with the **token** (`123456789:AA…`).
2. At BotFather send `/setprivacy` → choose the bot → **Disable**. Otherwise the bot doesn't see every
   reply in groups (e.g. replies to its feedback questions).
3. Invite the bot to your group. *If you change the privacy mode after adding the bot: remove the bot from the group
   once and invite it again – otherwise the change won't take effect.*

The setup assistant finds the group's chat id for you. Only if you set `TELEGRAM_CHAT_ID` by hand (e.g. in
`.env`): group ids are **negative**, and larger groups ("supergroups") start with `-100`. Telegram Web shows
the id at the end of the address when you open the group (`…/#-1001234567890`).

## 2. Claude API key

Corsarr uses Claude to understand messages, pick suggestions and write its replies. For that it needs an API
key from Anthropic. This is separate from a Claude.ai chat subscription – the API is billed per use from
prepaid credit. A few dollars last a long time ([what does it cost?](Home.md#what-does-it-cost)).

1. Open the [Claude Console](https://console.anthropic.com/) and sign up or log in.
2. **Add credit:** in the Console's billing settings, add a payment method and buy credit (e.g. $5). The API
   only works once there is credit on the account.
3. **Set a limit:** under *Limits*, set a **monthly spend limit** (e.g. $2). Corsarr can then never cost more
   than that, whatever happens.
4. **Create the key:** under *API Keys* click *Create Key*, give it a name such as `Corsarr` and create it.
5. **Copy the key right away** – it starts with `sk-ant-` and is shown only once. If you lose it, simply
   delete it and create a new one.
6. Paste it in the assistant's *AI provider* step, or under *Configuration → AI provider*.

Keep the key private: anyone who has it can spend your credit. Corsarr stores it only in its data directory
and never shows it again in the web interface.

If the limit is reached or the API is unreachable, the bot says so once in the group, stops taking requests
and checks every 5 minutes whether the API is available again. Once it is, the bot announces that it is back.

### Other providers (untested)

Instead of Claude you can select OpenAI, Google Gemini, Ollama or LM Studio. **These are not tested** –
suggestions may be worse or fail, and Claude is recommended. Where to get what you need:

- **OpenAI:** API key from [platform.openai.com](https://platform.openai.com/) (*API keys*); prepaid credit
  and a usage limit are set there too.
- **Gemini:** API key from [Google AI Studio](https://aistudio.google.com/) (*Get API key*).
- **Ollama:** install [Ollama](https://ollama.com/), pull a model (`ollama pull <model>`), make it reachable
  from the network (`OLLAMA_HOST=0.0.0.0`) and enter `http://<ip>:11434`.
- **LM Studio:** download a model in [LM Studio](https://lmstudio.ai/), start the local server (*Developer* →
  *Start server*, enable *Serve on local network*) and enter `http://<ip>:1234`.

Details and limitations: [Configuration](Configuration.md#ai-provider).

## 3. Jellyfin

- **API key:** Jellyfin dashboard → *API Keys* → **+** → name e.g. `Corsarr`.
- **Account:** the shared Jellyfin user you all watch with (e.g. `LivingRoom`) – once address and API key
  are entered, Corsarr offers the existing accounts to pick from. From this account the bot reads what you
  watched, what you started and what your favourites (♥) are.
- **Address:** e.g. `http://192.168.1.20:8096` – the address the bot uses to reach the server on your network.

## 4. Jellyseerr / Seerr

- **API key:** *Settings* → *General* → *API Key*.
- **Address:** e.g. `http://192.168.1.21:5055`.

The bot requests new titles with this key – they show up like any other request. Jellyseerr has been merged
into Seerr; Seerr and Overseerr use the same API and are set up the same way.

## Without the assistant

Everything can also be entered under **Configuration**, which has one collapsible section per service
(→ [Configuration](Configuration.md#the-web-interface)). Fill in *Telegram*, *AI provider*, *Jellyfin* and
*Jellyseerr / Seerr*, then **Save** – the bot starts right away. Under **Status** every connection should
turn green; if not, the reason is shown there (→ [Troubleshooting](Troubleshooting.md)). The webhooks are
described in [Webhooks](Webhooks.md).

## Optional

- **Language:** the bot speaks English and German and automatically answers in the language you write in.
  *Configuration → Interface and updates → Language* sets the language of the web interface, and of the bot
  until someone writes to it.
- **Characters:** *Configuration → Bot behaviour* → pirate / Gen Z on or off. Also possible in the chat:
  `@bot no more pirate`.
- **Password for the web interface:** by default there is no login (it's intended for use on your home
  network). Set one under *Configuration → Interface and updates*. It is also required for downloading
  backups.
