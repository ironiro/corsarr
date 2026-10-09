# Installation

There are three ways to install Corsarr, depending on your setup. They all end the same way: the web interface is available
at `http://<ip>:8787/` and its setup assistant guides you through the rest (→ [Setup](Setup.md)). No config file needed.

| Method | Best for |
| --- | --- |
| [Proxmox LXC](#proxmox-lxc-recommended) | Proxmox servers – one command in the host shell creates the container |
| [Docker](#docker) | Any Docker host, Synology, Unraid, Raspberry Pi |
| [Manual](#manual-install-on-linux) | Other Linux machines with systemd |
| [macOS / Windows](#macos-and-windows-for-trying-it-out) | Trying it out on your own computer |

The bot must **reach Jellyfin and Jellyseerr**, and **Jellyfin, Sonarr and Radarr must reach the bot**
(webhooks on port 8787). The simplest setup is to have everything on the same home network.

---

## Proxmox LXC (recommended)

### Install with one command

Open the shell of your **Proxmox host** (*Datacenter → your node → Shell*) and run:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/proxmox.sh)"
```

The script
1. downloads the Debian 12 container template if it isn't there yet,
2. creates an unprivileged container named `corsarr` (next free id, 1 core, 512 MB RAM, 4 GB disk, DHCP,
   starts on boot),
3. installs Corsarr inside it (see [what the installer does](#what-the-installer-does-inside-the-container)),
4. prints the address of the web interface.

Nothing is installed on the Proxmox host itself.

**Getting into the container:** the container has no root password. Its console in the Proxmox UI logs
in as root automatically (only Proxmox admins can open it); from the host shell use `pct enter <id>`.
To log in with a password instead, set one with `pct exec <id> -- passwd root`.

**Static IP (recommended):** Jellyfin, Sonarr and Radarr use the container's address for their webhooks, so
it should not change. Either reserve the address in your router, or set it when creating the container:

```bash
CORSARR_IP=192.168.1.50/24 CORSARR_GW=192.168.1.1 bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/proxmox.sh)"
```

Other settings, all optional: `CORSARR_CTID` (container id), `CORSARR_HOSTNAME`, `CORSARR_STORAGE` (storage for
the disk, default: the first active one for containers), `CORSARR_DISK` (GB), `CORSARR_CORES`, `CORSARR_RAM`
(MB), `CORSARR_BRIDGE` (default `vmbr0`), `CORSARR_BRANCH`.

### Update

In the Proxmox host shell (replace `105` with your container id):

```bash
pct exec 105 -- bash -c "curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh | bash"
```

Code and dependencies are updated, settings and data are kept, and the service restarts.

### Remove

```bash
pct stop 105 && pct destroy 105
```

This deletes the container including all Corsarr data.

### Alternative: install into an existing container

If you prefer to create the container yourself (*Create CT* in the Proxmox UI: Debian 12 or Ubuntu
22.04/24.04, unprivileged, 1 core, 512 MB, 4 GB), open **the container's** console and run:

```bash
apt-get update && apt-get install -y curl
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh)"
```

> Run this inside the container, not in the Proxmox host shell. On a Proxmox host the script stops and points
> you to the one-command install above.

### What the installer does inside the container

1. installs `git` and `python3-venv`,
2. creates the system user `corsarr`,
3. downloads the code to `/opt/corsarr` and sets up its own Python environment,
4. sets up the systemd service `corsarr` (starts automatically, including after a container restart),
5. prints the address of the web interface.

Data (database, logs, settings from the web interface) lives in `/var/lib/corsarr` inside the container.
Running `install.sh` again updates Corsarr. It accepts `CORSARR_BRANCH`, `CORSARR_DIR` (code directory),
`CORSARR_DATA` (data directory), `CORSARR_PORT` (for the startup check if you changed the port) and
`CORSARR_REPO` (your own fork).

---

## Docker

### With Docker Compose

Download `docker-compose.yml` from the repository, then in the same directory:

```bash
docker compose up -d
```

The image `ghcr.io/ironiro/corsarr:latest` (newest stable release) is available for amd64 and arm64
(Raspberry Pi 4/5). For pre-releases use `:beta`, for every commit on `main` `:edge`, for a fixed version
e.g. `:1.2.0` – see [release channels](Operations.md#release-channels). If it
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
