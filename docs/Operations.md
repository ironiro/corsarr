# Operations

## Where things live

| | LXC / manual | Docker | run manually |
| --- | --- | --- | --- |
| Code | `/opt/corsarr` | in the image | repository directory |
| Data | `/var/lib/corsarr` | volume `corsarr-data` → `/data` | `./data` |

In the data directory:

| File | Contents |
| --- | --- |
| `corsarr.db` | SQLite: suggestions, rejections, ratings, traits, bot behaviour, download notifications |
| `config.json` | Settings from the web interface – **contains API keys**, readable by the owner only |
| `logs/corsarr.log` | Log, rotated at 2 MB, 5 old files kept |

## Logs

- Web interface → **Events** (the last 1000 entries since start)
- LXC: `journalctl -u corsarr -f`
- Docker: `docker compose logs -f`
- File: `logs/corsarr.log` in the data directory

For more detail, set *Configuration → System → Log level* to `DEBUG` in the web interface.

## Start, stop, restart

| | LXC / manual | Docker |
| --- | --- | --- |
| Status | `systemctl status corsarr` | `docker compose ps` |
| Restart | `systemctl restart corsarr` | `docker compose restart` |
| Stop | `systemctl stop corsarr` | `docker compose stop` |

To restart only the Telegram part (e.g. after a network problem): web interface → Status → *Restart bot*.

## Update

- **LXC:** in the Proxmox host shell `pct exec <id> -- bash -c "curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh | bash"` (see [Installation](Installation.md#update)).
- **Docker:** `docker compose pull && docker compose up -d`
- **Manual:** `cd /opt/corsarr && git pull && .venv/bin/pip install -r requirements.txt && systemctl restart corsarr`

The database is upgraded automatically on start. Settings are kept.

## Backup

It is enough to back up the data directory – most importantly `corsarr.db` and `config.json`.

```bash
# LXC – while running, consistent thanks to SQLite's backup (once: apt install sqlite3)
sqlite3 /var/lib/corsarr/corsarr.db ".backup /root/corsarr-backup.db"
cp /var/lib/corsarr/config.json /root/corsarr-config.json
```

Proxmox: a normal container backup (vzdump) contains everything.

## Moving (e.g. from a laptop to the server)

1. [Install](Installation.md) on the new machine.
2. Stop the service there, copy `corsarr.db` and `config.json` from the old data directory into the new one
   and fix the file ownership (LXC: `chown corsarr:corsarr /var/lib/corsarr/*`, Docker directory: `chown 1000:1000`).
3. Start the service. Learned taste, settings and the webhook secret are carried over.
4. In **Jellyfin, Sonarr and Radarr** change the webhook address to the new IP.
5. Stop the old bot – two bots with the same token interfere with each other.

Upgrading from the old name Filmbot: a `filmbot.db` in the data directory is automatically adopted as
`corsarr.db` on the first start.

## Keeping an eye on costs

The [Claude Console](https://console.anthropic.com/) shows actual costs under *Usage* and the monthly limit
under *Limits*. The automatic status checks cost nothing; you only pay when the bot interprets or writes
messages.
