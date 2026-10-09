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

### Release channels

| Channel | What you get | Docker image |
| --- | --- | --- |
| **Stable** (default) | Tested releases (`v1.2.0`) | `:latest` or `:stable` |
| **Beta** | Pre-releases (`v1.3.0-beta.1`) and every stable release – new features earlier, may have bugs | `:beta` |
| **Development** | Every commit on `main` – for developers | `:edge` |

Choose the channel under **Configuration → System → Update channel**. With Docker the channel follows the
image tag in `docker-compose.yml`; a fixed version such as `:1.2.0` never changes on its own.

Before every update the installer copies the database and settings to `backups/pre-update-<date>-<version>/`
in the data directory (the last five are kept). Each version knows which database format it understands: if
you switch from beta back to an older stable version and the beta already converted the database, Corsarr
refuses to start and says so in the web interface instead of damaging the data. Then either update to the
newer version again, or stop Corsarr and copy `corsarr.db` and `config.json` back from the backup made
before the update. Configurations from older versions are always taken over.

### Updating

**From the web interface:** under **Status → Version** Corsarr shows the installed version, the newest one in
your channel with its release notes (or the list of commits on the development channel). With the LXC/Proxmox installation, **Update now** installs it:
the app drops a request file, a small root service (`corsarr-update.path`) runs the installer, and Corsarr
restarts. The page reconnects by itself and shows the installer's output. With Docker or a manual install,
the page shows the command to run instead.

Installations made before this feature existed get the update service with the next command-line update
below; after that the button works.

On the command line:

- **LXC:** in the Proxmox host shell `pct exec <id> -- bash -c "curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh | bash"` (see [Installation](Installation.md#update)).
- **Docker:** `docker compose pull && docker compose up -d`
- **Manual:** `cd /opt/corsarr && git fetch --tags && git checkout v1.2.0 && .venv/bin/pip install -r requirements.txt && systemctl restart corsarr` (or `git pull` on `main` for the development version)

The database is upgraded automatically on start. Settings are kept.

## Backup

**In the web interface (tab *Backup*):** download an encrypted zip with all settings, ratings and the learned
taste. It contains the credentials that are set – Telegram bot token, the AI provider's API key, the Jellyfin and
Jellyseerr API keys, the webhook secret and the web interface password – but not the Sonarr/Radarr API keys,
which Corsarr never saves. It needs a password for the web interface (`ADMIN_PASSWORD`), because the file
holds the API keys, and you choose a separate password for the zip itself. To restore – also on a fresh
installation on another machine – upload the zip there and enter that password (on a fresh installation the
setup assistant offers this as the first step). Backups from older versions are taken over; a backup from a
newer version is refused until you update. The data being replaced is kept in `backups/pre-restore-…`.
The zip uses AES encryption, which macOS's Archive Utility cannot open (7-Zip, Keka and `7z` can) – Corsarr
itself doesn't need that.

**By hand:** it is enough to back up the data directory – most importantly `corsarr.db` and `config.json`.

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
