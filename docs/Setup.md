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

## 2. Claude API

1. Create an **API key** in the [Claude Console](https://console.anthropic.com/) (`sk-ant-…`).
2. Under *Limits* set a **monthly spend limit**, e.g. $2.

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

- **Language:** the bot automatically replies in whatever language you write to it in (English or German).
  Configuration → *System* → Language sets the language of the web interface, and of the bot until someone
  writes to it.
- **Characters:** Configuration → *Bot behaviour* → pirate / Gen Z on or off. Also possible in the chat:
  `@bot no more pirate`.
- **Password for the web interface:** by default there is no login (it's intended for use on your home network). A password can be
  set under Configuration → *Web server and webhook*.
