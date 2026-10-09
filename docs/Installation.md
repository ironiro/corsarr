# Installation

There are three ways to install Corsarr, depending on your setup. They all end the same way: the bot starts, the web interface is available
at `http://<ip>:8787/`, and everything else is entered there (→ [Setup](Setup.md)). No config file needed.

| Method | Best for |
| --- | --- |
| [Proxmox LXC](#proxmox-lxc-recommended) | Proxmox servers – one command, updates with the same command |
| [Docker](#docker) | Any Docker host, Synology, Unraid, Raspberry Pi |
| [Manual](#manual-install-on-linux) | Other Linux machines with systemd |
| [macOS / Windows](#macos-and-windows-for-trying-it-out) | Trying it out on your own computer |

The bot must **reach Jellyfin and Jellyseerr**, and **Jellyfin, Sonarr and Radarr must reach the bot**
(webhooks on port 8787). The simplest setup is to have everything on the same home network.

---

## Proxmox LXC (recommended)

### 1. Create the container

In the Proxmox UI, *Create CT*:

| Setting | Value |
| --- | --- |
| Template | Debian 12 (or Ubuntu 22.04/24.04) |
| Unprivileged container | yes |
| Disk | 4 GB |
| CPU | 1 core |
| Memory | 512 MB (swap 512 MB) |
| Network | DHCP or static IP – **static recommended**, since Jellyfin, Sonarr and Radarr use it as the webhook address |

Start the container and log in as root in the Proxmox *Console*.

### 2. Install

```bash
apt-get update && apt-get install -y curl
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh)"
```

The script
1. installs `git` and `python3-venv`,
2. creates the system user `corsarr`,
3. downloads the code to `/opt/corsarr` and sets up its own Python environment,
4. sets up the systemd service `corsarr` (starts automatically, including after a container restart),
5. prints the address of the web interface.

Data (database, logs, settings from the web interface) lives in `/var/lib/corsarr`.

### 3. Update

Run the same command again. Code and dependencies are updated, settings and data are kept, and the service
restarts.

### Advanced options (optional)

You can set these environment variables before running the script: `CORSARR_BRANCH` (another branch), `CORSARR_DIR` (code directory),
`CORSARR_DATA` (data directory), `CORSARR_PORT` (for the startup check if you changed the port),
`CORSARR_REPO` (your own fork). Example:

```bash
CORSARR_BRANCH=dev bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh)"
```

---

## Docker

### With Docker Compose

Download `docker-compose.yml` from the repository, then in the same directory:

```bash
docker compose up -d
```

The image `ghcr.io/ironiro/corsarr:latest` is available for amd64 and arm64 (Raspberry Pi 4/5). If it
cannot be pulled, Compose can build it from source instead (this needs a clone of the whole repository):
`docker compose up -d --build`.

| What | Where |
| --- | --- |
| Web interface + webhooks | port 8787 |
| Data | volume `corsarr-data` (mounted as `/data`) |
| Time zone | `TZ` in the compose file, default `Europe/Berlin` |
| Logs | `docker compose logs -f` |
| Update | `docker compose pull && docker compose up -d` |

### Without Compose

```bash
docker run -d --name corsarr --restart unless-stopped \
  -p 8787:8787 -v corsarr-data:/data -e TZ=Europe/Berlin \
  ghcr.io/ironiro/corsarr:latest
```

### Using a directory instead of a volume

To keep the data in a specific directory (e.g. for backups): `-v /path/to/corsarr:/data`. The container
runs as user id 1000, so the directory must be owned by that user: `sudo chown 1000:1000 /path/to/corsarr`.

---

## Manual install on Linux

For Linux machines with systemd where the install script doesn't work (e.g. other distributions). Requires
Python 3.10+, `git`, `python3-venv`.

```bash
sudo useradd --system --home-dir /var/lib/corsarr --shell /usr/sbin/nologin corsarr
sudo mkdir -p /var/lib/corsarr && sudo chown corsarr:corsarr /var/lib/corsarr
sudo git clone https://github.com/ironiro/corsarr.git /opt/corsarr
cd /opt/corsarr && sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt
sudo cp deploy/corsarr.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now corsarr
```

Update: `cd /opt/corsarr && sudo git pull && sudo .venv/bin/pip install -r requirements.txt && sudo systemctl restart corsarr`

---

## macOS and Windows (for trying it out)

The bot works the same way there, but only while the terminal window is open – for permanent use, a server is better.

**macOS** (Python from Homebrew; the preinstalled 3.9 is too old):

```bash
brew install python@3.12 git
git clone https://github.com/ironiro/corsarr.git && cd corsarr
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m corsarr
```

> **Important:** start it in the built-in **Terminal**, not in iTerm. Otherwise macOS blocks access to
> Jellyfin and Jellyseerr on your home network (→ [Troubleshooting](Troubleshooting.md#no-route-to-host-on-macos)).

**Windows** (Python from python.org, tick *Add python.exe to PATH* during installation), PowerShell:

```powershell
git clone https://github.com/ironiro/corsarr.git; cd corsarr
py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m corsarr
```

Data then lives in the `data` directory next to the code. Stop with Ctrl+C.
