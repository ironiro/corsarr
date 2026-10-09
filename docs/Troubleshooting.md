# Troubleshooting

The first place to look is always the web interface: **Status** shows the exact error message per
connection, **Events** shows what happened.

## The web interface says "No connection to the bot"

The program is not running (anymore). LXC: `systemctl status corsarr` and `journalctl -u corsarr -n 50`.
Docker: `docker compose logs --tail 50`. Run manually: is the terminal window still open?

## The bot doesn't start, status "not set up"

Required values are missing – Status lists them. Run the setup assistant (tab *Setup*), or enter them under
Configuration and save.

## Telegram

| Message | Cause / fix |
| --- | --- |
| `InvalidToken` | Token copied incorrectly. Get it again from @BotFather (`/token`). |
| `Chat not found` | Wrong chat id, or the bot is not in the group. Run the Telegram step of the setup assistant again – it finds the group once someone writes in it. |
| `privacy mode ON` | At @BotFather `/setprivacy` → bot → **Disable**, then remove the bot from the group and invite it again. |
| `Conflict: terminated by other getUpdates request` | The same bot is running twice (e.g. old laptop and new server). Stop one. |

**The bot doesn't react in the group:** it only reacts to mentions (`@yourbot`) and replies to its messages –
and only in the configured group. Every incoming message is listed under Events.

## Claude

| Message | Cause / fix |
| --- | --- |
| `authentication_error`, `invalid x-api-key` | Wrong or deleted key – create a new one. |
| `credit balance`, `usage limit` | Monthly limit reached or no credit. Raise it in the Claude Console; the bot comes back on its own. |
| `not_found_error` for the model | `CLAUDE_MODEL` misspelled. Clear the field to use the default. |

## Other AI providers (untested)

| Message | Cause / fix |
| --- | --- |
| `ConnectError` / `Connection refused` (Ollama, LM Studio) | Server not running, or only listening on localhost. Use the machine's IP, not `localhost`, when Corsarr runs in Docker/LXC; for Ollama set `OLLAMA_HOST=0.0.0.0`. |
| `model … not found` | Model not pulled/downloaded, or misspelled – pick it from the list. |
| `HTTP 401` / `HTTP 403` | Wrong API key. |
| `HTTP 429` | Rate limit or no credit left with the provider. |
| "That didn't work" in the chat, `answer does not match the format` in the events | The model did not return the JSON structure Corsarr needs. Try a larger model, or switch back to Claude. |

## Jellyfin / Jellyseerr / Seerr

| Message | Cause / fix |
| --- | --- |
| `ConnectError … (Connection refused)` | Service not running or wrong port. |
| `ConnectError … (No route to host)` | Machine unreachable – wrong IP, different network, or the macOS block (see below). |
| `ConnectError … nodename nor servname` | Host name can't be resolved – use the IP address instead. |
| `HTTP 401` | Wrong API key. |
| `Jellyfin user … not found` | `JELLYFIN_USER` doesn't match a user name. |

### "No route to host" on macOS

macOS only lets programs reach devices on the home network with the *Local Network* permission. When the
bot is started in **iTerm**, Python doesn't get this permission – and doesn't ask for it either ("Python"
never appears in the list). Telegram and Claude still work because they go over the internet.

**Fix:** start the bot in the built-in **Terminal**. If macOS asks for permission the first time, click *Allow*.

## Suggestions don't match the genre

Events lists every request with the genre that was understood, e.g.
`Message from Sam: “find horror” → intent recommend, type ['movie'], genres ['Horror']`.
If it says `genres *`, no genre was recognised – phrase the request more clearly. Every suggestion is listed
there with its reason, too.

## Webhooks don't arrive

If the tile stays at "unknown":
- Jellyfin/Sonarr/Radarr need the address **of the bot**, not of the service itself.
- Check reachability from the service's machine: `curl http://<bot-ip>:8787/health` must return `ok`.
- Jellyfin: *Playback Stop* ticked? Correct account in the user filter? Destination saved?

The active checks name what is wrong, e.g. "No destination in the Webhook plugin points at Corsarr",
"“Playback Stop” is not selected", "No Corsarr webhook with the current secret found" or "The test doesn't
reach Corsarr" (Sonarr/Radarr can't reach Corsarr's address – check IP, port and firewall).

If the tile turns red and shows "wrong secret", copy the address or secret again from *Setup → Webhooks*.
For Sonarr and Radarr, *Set up in Sonarr* / *Set up in Radarr* there rewrites the webhook with the correct
values.

## Jellyfin doesn't start after installing a plugin

Typical log (`journalctl -u jellyfin -n 40 --no-pager`): `Plugin installed` → `Sending shutdown
notifications` → an `[FTL]` line while shutting down (e.g. caused by IntroSkipper) → `Deactivated
successfully`, and no start afterwards. Jellyfin isn't broken, just stopped:

```bash
systemctl start jellyfin
```

In future, restart Jellyfin on the server with `systemctl restart jellyfin` rather than from the dashboard.

## Too many download notifications

Remove the old Telegram connections in Sonarr/Radarr – otherwise everything arrives twice (the setup assistant
lists them when it sets up the webhook). For the Corsarr webhook, tick only *On File Import*.

## Backup and restore

| Message / problem | Cause / fix |
| --- | --- |
| "Backups need a password for this interface" | Set `ADMIN_PASSWORD` under *Configuration → Interface and updates*, then sign in. |
| "Wrong password for this backup" | Enter the password chosen when the backup was made, not the web interface password. |
| "The backup was made by a newer Corsarr version" | Update Corsarr to that version first, then restore. |
| macOS can't open the zip | Archive Utility doesn't support AES encryption. Use 7-Zip, Keka or `7z` – or just restore it in Corsarr. |
