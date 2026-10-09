#!/usr/bin/env bash
# Corsarr – install or update on Debian/Ubuntu (e.g. a Proxmox LXC container).
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh)"
#
# Run as root. Running it again updates to the newest version of the update channel chosen in the web
# interface (stable by default); settings and data are kept, and a backup is made before every update.
# Overridable: CORSARR_REF (a release tag like v1.2.0, or "main"), CORSARR_CHANNEL (stable/beta/dev, for a
# new installation), CORSARR_REPO, CORSARR_BRANCH, CORSARR_DIR (code), CORSARR_DATA (data), CORSARR_PORT.
set -euo pipefail
# A locale every Debian/Ubuntu has – avoids "Setting locale failed" warnings when the calling shell
# (SSH, Proxmox console) uses a locale that isn't installed here.
export LANG=C.UTF-8 LC_ALL=C.UTF-8

REPO="${CORSARR_REPO:-https://github.com/ironiro/corsarr.git}"
BRANCH="${CORSARR_BRANCH:-main}"
APP_DIR="${CORSARR_DIR:-/opt/corsarr}"
DATA_DIR="${CORSARR_DATA:-/var/lib/corsarr}"
PORT="${CORSARR_PORT:-8787}"
SERVICE=corsarr
SVC_USER=corsarr

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# Copy of the database and settings before an update, so switching back to an older version (or a
# failed update) can be undone. Keeps the last 5 in $DATA_DIR/backups; readable only by the service user.
backup_before_update() {
    [ -f "$DATA_DIR/corsarr.db" ] || return 0
    local from target
    from="$(cat "$APP_DIR/.corsarr-version" 2>/dev/null || git -C "$APP_DIR" rev-parse --short HEAD)"
    target="$DATA_DIR/backups/pre-update-$(date +%Y%m%d-%H%M%S)-$from"
    say "Backing up data to $target …"
    mkdir -p "$target"
    # SQLite's backup API: a consistent copy even while the bot is writing
    python3 -c 'import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()' \
        "$DATA_DIR/corsarr.db" "$target/corsarr.db"
    [ -f "$DATA_DIR/config.json" ] && cp "$DATA_DIR/config.json" "$target/"
    chmod -R go-rwx "$DATA_DIR/backups"
    chown -R "$SVC_USER:$SVC_USER" "$DATA_DIR/backups"
    ls -1d "$DATA_DIR"/backups/pre-update-* | sort | head -n -5 | xargs -r rm -rf
}

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

# --- what to install: a release tag, or the main branch for the dev channel -------------------------
CHANNEL="${CORSARR_CHANNEL:-}"
if [ -z "$CHANNEL" ] && [ -f "$DATA_DIR/config.json" ]; then  # the channel chosen in the web interface
    CHANNEL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("UPDATE_CHANNEL", ""))' \
        "$DATA_DIR/config.json" 2>/dev/null || true)"
fi
CHANNEL="${CHANNEL:-stable}"
REF="${CORSARR_REF:-}"
if [ -z "$REF" ]; then
    if [ "$CHANNEL" = "dev" ]; then
        REF="$BRANCH"
    else
        # Newest release tag of the channel; betas (vX.Y.Z-beta.N) sort before their release.
        REF="$(git ls-remote --tags --refs "$REPO" 'v*' | python3 -c '
import re, sys
beta = sys.argv[1] == "beta"
tags = []
for line in sys.stdin:
    m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?", line.split("refs/tags/")[-1].strip())
    if m and (beta or m.group(4) is None):
        tags.append(((int(m[1]), int(m[2]), int(m[3]), int(m[4]) if m[4] else 10**9), m.group(0)))
print(max(tags)[1] if tags else "")' "$CHANNEL")" || die "Could not reach $REPO."
        if [ -z "$REF" ]; then
            say "No $CHANNEL release yet – installing the development version."
            REF="$BRANCH"
        fi
    fi
fi
# The web interface passes the target through a file the service user can write: accept only tag/branch names.
echo "$REF" | grep -Eq '^(v[0-9]+\.[0-9]+\.[0-9]+(-beta\.[0-9]+)?|[A-Za-z0-9][A-Za-z0-9._-]*)$' || die "Invalid version: $REF"

UPDATE=0
if [ -d "$APP_DIR/.git" ]; then
    UPDATE=1
    backup_before_update
    say "Updating code ($APP_DIR) to $REF …"
    git -C "$APP_DIR" fetch --quiet --tags --force origin "$BRANCH"
else
    say "Downloading code to $APP_DIR ($REF) …"
    git clone --quiet "$REPO" "$APP_DIR"
fi
if [ "$REF" = "$BRANCH" ]; then
    git -C "$APP_DIR" checkout --quiet "$BRANCH"
    git -C "$APP_DIR" reset --quiet --hard "origin/$BRANCH"
    rm -f "$APP_DIR/.corsarr-version"
else
    git -C "$APP_DIR" rev-parse --quiet --verify "refs/tags/$REF" >/dev/null || die "Version $REF not found."
    git -C "$APP_DIR" checkout --quiet --force --detach "refs/tags/$REF"
    echo "$REF" > "$APP_DIR/.corsarr-version"  # shown as the version in the web interface
fi

say "Setting up Python environment …"
[ -x "$APP_DIR/.venv/bin/python" ] || python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

say "Setting up service …"
# corsarr.service runs the bot; corsarr-update.path/.service let the web interface trigger updates.
for unit in corsarr.service corsarr-update.service corsarr-update.path; do
    sed -e "s#/opt/corsarr#$APP_DIR#g" -e "s#/var/lib/corsarr#$DATA_DIR#g" \
        -e "s#CORSARR_BRANCH=main#CORSARR_BRANCH=$BRANCH#g" \
        "$APP_DIR/deploy/$unit" > "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --quiet "$SERVICE"
systemctl enable --now --quiet corsarr-update.path
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
        echo "    Version:        $REF"
        echo "    Update:         in the web interface, or run this script again"
        exit 0
    fi
    sleep 1
done
die "The service does not respond. Details: journalctl -u $SERVICE -n 50 --no-pager"
