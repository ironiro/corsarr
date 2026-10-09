# Setup

After [installation](Installation.md), only the web interface is running at first. Its **Status** page shows
what is still missing. This page explains where each value comes from.

## 1. Create the Telegram bot

1. In Telegram open **@BotFather** and send `/newbot`. Choose a name and a username (ending in `bot`).
   BotFather replies with the **token** (`123456789:AA…`).
2. At BotFather send `/setprivacy` → choose the bot → **Disable**. Otherwise the bot doesn't see every
   reply in groups (e.g. replies to its feedback questions).
3. Invite the bot to your group. *If you change the privacy mode after adding the bot: remove the bot from the group
   once and invite it again – otherwise the change won't take effect.*

### Finding the group's chat id

- **Telegram Web:** open <https://web.telegram.org/a/> and click the group. The address ends in the id,
  e.g. `…/#-1001234567890`.
- **Telegram Desktop:** Settings → Advanced → Experimental settings → enable *Show Peer IDs in Profile*,
  then open the group's profile.

Group ids are always **negative**. Larger groups ("supergroups") start with `-100`; if the client shows the
number without `-100`, put it in front. If the status later says "Chat not found", that is almost always
the cause.

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
6. Paste it in Corsarr's web interface under *Configuration → Claude API → API key* and save.

Keep the key private: anyone who has it can spend your credit. Corsarr stores it only in its data directory
and never shows it again in the web interface.

If the limit is reached or the API is unreachable, the bot says so once in the group, stops taking requests
and checks every 5 minutes whether the API is available again. Once it is, the bot announces that it is back.

## 3. Jellyfin

- **API key:** Jellyfin dashboard → *API Keys* → **+** → name e.g. `Corsarr`.
- **Account:** the name of the shared Jellyfin user you all watch with (e.g. `LivingRoom`). From this account the bot
  reads what you watched, what you started and what your favourites (♥) are.
- **Address:** e.g. `http://192.168.1.20:8096` – the address the bot uses to reach the server on your network.

## 4. Jellyseerr

- **API key:** Jellyseerr → *Settings* → *General* → *API Key*.
- **Address:** e.g. `http://192.168.1.21:5055`.

The bot requests new titles with this key – they show up in Jellyseerr like any other request.

## 5. Enter everything in the web interface

1. Open `http://<bot-ip>:8787/` → **Configuration**.
2. Fill in the fields under *Telegram*, *Claude API*, *Jellyfin* and *Jellyseerr*, then **Save**. The bot
   starts right away.
3. Under **Status**, Telegram, Claude, Jellyfin and Jellyseerr should all be green. If not, the reason is shown
   there (→ [Troubleshooting](Troubleshooting.md)).
4. Try it in the group: `@yourbot find a movie for tonight`.

Then set up the [webhooks](Webhooks.md) – for feedback after watching (Jellyfin) and download notifications
(Sonarr/Radarr).

## Optional

- **Language:** the bot speaks English and German and automatically answers in the language you write in.
  Configuration → *System* → Language sets the language of the web interface, and of the bot until someone
  writes to it.
- **Characters:** Configuration → *Bot behaviour* → pirate / Gen Z on or off. Also possible in the chat:
  `@bot no more pirate`.
- **Password for the web interface:** by default there is no login (it's intended for use on your home network). A password can be
  set under Configuration → *Web server and webhook*.
