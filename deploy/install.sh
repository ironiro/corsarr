#!/usr/bin/env bash
# Corsarr – install or update on Debian/Ubuntu (e.g. a Proxmox LXC container).
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh)"
#
# Run as root. Running it again updates to the latest version; settings and data are kept.
# Overridable: CORSARR_REPO, CORSARR_BRANCH, CORSARR_DIR (code), CORSARR_DATA (data), CORSARR_PORT.
set -euo pipefail

REPO="${CORSARR_REPO:-https://github.com/ironiro/corsarr.git}"
BRANCH="${CORSARR_BRANCH:-main}"
APP_DIR="${CORSARR_DIR:-/opt/corsarr}"
DATA_DIR="${CORSARR_DATA:-/var/lib/corsarr}"
PORT="${CORSARR_PORT:-8787}"
SERVICE=corsarr
SVC_USER=corsarr

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Please run as root."
# Never install directly on a Proxmox VE host – that is what deploy/proxmox.sh (a container) is for.
if [ -d /etc/pve ] || command -v pveversion >/dev/null 2>&1; then
    die "This is a Proxmox VE host. To create a container with Corsarr, run instead:
    bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/proxmox.sh)\""
fi
command -v apt-get >/dev/null || die "Only for Debian/Ubuntu (apt)."
command -v systemctl >/dev/null || die "systemd is missing – for Docker use docker-compose.yml instead."

say "Installing packages (git, python3-venv) …"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv ca-certificates curl >/dev/null

python3 - <<'PY' || die "Python 3.10 or newer required (Debian 12 / Ubuntu 22.04 or newer)."
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY

if ! id "$SVC_USER" >/dev/null 2>&1; then
    say "Creating system user $SVC_USER …"
    useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
fi
mkdir -p "$DATA_DIR"
chown "$SVC_USER:$SVC_USER" "$DATA_DIR"

UPDATE=0
if [ -d "$APP_DIR/.git" ]; then
    UPDATE=1
    say "Updating code ($APP_DIR) …"
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    git -C "$APP_DIR" checkout --quiet "$BRANCH"
    git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"
else
    say "Downloading code to $APP_DIR …"
    git clone --quiet --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

say "Setting up Python environment …"
[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

say "Setting up service …"
sed -e "s#/opt/corsarr#$APP_DIR#g" -e "s#/var/lib/corsarr#$DATA_DIR#g" \
    "$APP_DIR/deploy/corsarr.service" > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl restart "$SERVICE"

say "Waiting for startup …"
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
        IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        echo
        if [ "$UPDATE" -eq 1 ]; then
            say "Updated and restarted."
        else
            say "Installed."
        fi
        echo "    Web interface:  http://${IP:-<ip-of-this-container>}:$PORT/"
        echo "    Logs:           journalctl -u $SERVICE -f"
        echo "    Update:         run this script again"
        exit 0
    fi
    sleep 1
done
die "The service does not respond. Details: journalctl -u $SERVICE -n 50 --no-pager"
