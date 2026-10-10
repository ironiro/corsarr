#!/usr/bin/env bash
# Corsarr – install or update on Debian/Ubuntu (e.g. a Proxmox LXC container).
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/install.sh)"
#
# Run as root. Running it again updates to the newest version of the update channel chosen in the web
# interface (stable by default); settings and data are kept, and a backup is made before every update.
# An update is prepared next to the running version (new code and a new Python environment) and only then
# switched over; if the new version does not start, the previous one is put back.
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
# Root-owned places the service user cannot touch: pre-update backups, the update log the web interface
# shows (written by corsarr-update.service), pip's download cache (the update service has no /root).
BACKUP_DIR=/var/backups/corsarr
LOG_DIR=/var/log/corsarr
ENV_FILE=/etc/corsarr.env
export PIP_CACHE_DIR=/var/cache/corsarr/pip
# Stalled downloads fail instead of hanging forever (corsarr-update.service has a timeout as well).
export GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=60
PIP_OPTS=(--quiet --timeout 60 --retries 3)

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
# Files in the data directory are read and written by the service user, never by root: root does not
# follow paths that user controls, and leaves nothing root-owned there (a root-owned corsarr.db-shm
# would lock the bot out of its own database).
as_svc() { runuser -u "$SVC_USER" -- "$@"; }

# --- backup before an update ------------------------------------------------------------------------
# Copy of the database and settings before an update, so switching back to an older version (or a
# failed update) can be undone. Keeps the last 5 in $BACKUP_DIR, readable by root only. The database is
# copied with SQLite's backup API (consistent even while the bot is writing) by the service user and
# streamed to root through a pipe.
BACKUP=""
backup_before_update() {
    [ -e "$DATA_DIR/corsarr.db" ] || return 0
    [ -L "$DATA_DIR/corsarr.db" ] && die "$DATA_DIR/corsarr.db is a symbolic link – refusing to back it up."
    [ -L "$BACKUP_DIR" ] && die "$BACKUP_DIR is a symbolic link."
    install -d -m 0700 "$BACKUP_DIR"
    BACKUP="$BACKUP_DIR/pre-update-$(date +%Y%m%d-%H%M%S)-$PREV_VERSION"
    say "Backing up data to $BACKUP …"
    mkdir -m 0700 "$BACKUP"
    as_svc python3 - "$DATA_DIR/corsarr.db" > "$BACKUP/corsarr.db" <<'PY'
import os, shutil, sqlite3, sys, tempfile
src = sys.argv[1]
os.close(os.open(src, os.O_RDONLY | os.O_NOFOLLOW))  # refuses a symbolic link
with tempfile.TemporaryDirectory() as tmp:
    copy = os.path.join(tmp, "corsarr.db")
    s, d = sqlite3.connect(f"file:{src}?mode=ro", uri=True), sqlite3.connect(copy)
    s.backup(d)
    d.close(); s.close()
    with open(copy, "rb") as f:
        shutil.copyfileobj(f, sys.stdout.buffer)
PY
    if [ -f "$DATA_DIR/config.json" ] && [ ! -L "$DATA_DIR/config.json" ]; then
        as_svc cat "$DATA_DIR/config.json" > "$BACKUP/config.json"
    fi
    chmod -R go-rwx "$BACKUP"
    # shellcheck disable=SC2012  # our own, plain directory names
    ls -1d "$BACKUP_DIR"/pre-update-* | sort | head -n -5 | xargs -r rm -rf
}

# Database format version (SQLite user_version); empty when there is no database yet.
schema_version() {
    [ -f "$DATA_DIR/corsarr.db" ] || return 0
    as_svc python3 -c 'import sqlite3,sys; c=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True); print(c.execute("PRAGMA user_version").fetchone()[0])' \
        "$DATA_DIR/corsarr.db" 2>/dev/null || true
}

# Put the pre-update database back while the service is stopped (root feeds the copy, the service user
# writes it; the write-ahead files belong to the replaced database and go too).
restore_database() {
    say "Restoring the database from $BACKUP …"
    # shellcheck disable=SC2016  # $1 is expanded by the inner bash
    as_svc bash -c 'cat > "$1/corsarr.db.restore" && rm -f "$1/corsarr.db-wal" "$1/corsarr.db-shm" && mv -f "$1/corsarr.db.restore" "$1/corsarr.db"' \
        _ "$DATA_DIR" < "$BACKUP/corsarr.db"
}

# --- code and Python environment ----------------------------------------------------------------------
# Each version gets its own Python environment ($APP_DIR/.venv-<commit>); $APP_DIR/.venv is a symbolic
# link to the current one. The new environment is complete before anything is switched, and the previous
# one stays until the new version is up.
NEW_VENV="" PREV_VENV=""

build_venv() {  # $1 = commit; sets NEW_VENV to the directory name
    NEW_VENV=".venv-$(git -C "$APP_DIR" rev-parse --short "$1")"
    [ "$NEW_VENV" = "$PREV_VENV" ] && NEW_VENV="$NEW_VENV.new"  # same version again: never touch the running one
    local req
    req="$(mktemp)"
    git -C "$APP_DIR" show "$1:requirements.txt" > "$req"  # the new version's requirements, before checkout
    rm -rf "${APP_DIR:?}/$NEW_VENV"
    python3 -m venv "$APP_DIR/$NEW_VENV"
    "$APP_DIR/$NEW_VENV/bin/pip" install "${PIP_OPTS[@]}" -r "$req"
    rm -f "$req"
}

switch_venv() {  # $1 = directory name under $APP_DIR
    if [ -d "$APP_DIR/.venv" ] && [ ! -L "$APP_DIR/.venv" ]; then
        # An environment from before versioned directories: keep it as the previous one.
        mv "$APP_DIR/.venv" "$APP_DIR/.venv-prev"
        PREV_VENV=.venv-prev
    fi
    ln -s "$1" "$APP_DIR/.venv.tmp"
    mv -T "$APP_DIR/.venv.tmp" "$APP_DIR/.venv"  # one rename: the link is never missing
}

checkout() {  # $1 = commit, $2 = release tag, or "" for the head of the branch (dev channel)
    git -C "$APP_DIR" checkout --quiet --force --detach "$1"
    if [ -n "$2" ]; then
        echo "$2" > "$APP_DIR/.corsarr-version"  # shown as the version in the web interface
    else
        rm -f "$APP_DIR/.corsarr-version"
        git -C "$APP_DIR" checkout --quiet -B "$BRANCH" "$1"
    fi
}

install_units() {
    # corsarr.service runs the bot; corsarr-update.path/.service let the web interface trigger updates.
    for unit in corsarr.service corsarr-update.service corsarr-update.path; do
        sed -e "s#/opt/corsarr#$APP_DIR#g" -e "s#/var/lib/corsarr#$DATA_DIR#g" \
            -e "s#CORSARR_BRANCH=main#CORSARR_BRANCH=$BRANCH#g" \
            "$APP_DIR/deploy/$unit" > "/etc/systemd/system/$unit"
    done
    systemctl daemon-reload
}

healthy() {  # waits up to 30 s for the service to answer
    local _i
    for _i in $(seq 1 30); do
        curl -fsS --max-time 5 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
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

python3 - <<'PY' || die "Python 3.11 or newer required (Debian 12 / Ubuntu 24.04 or newer)."
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
PY

if ! id "$SVC_USER" >/dev/null 2>&1; then
    say "Creating system user $SVC_USER …"
    useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SVC_USER"
fi
mkdir -p "$DATA_DIR"
chown "$SVC_USER:$SVC_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"
install -d -m 0755 "$LOG_DIR"
install -d -m 0700 "$PIP_CACHE_DIR"
if [ ! -e "$ENV_FILE" ]; then
    # Optional KEY=value settings read by corsarr.service; may hold secrets, so root only.
    printf '# Corsarr: optional settings, one KEY=value per line (template: .env.example in %s).\n# Everything can be set in the web interface instead. Restart after changes: systemctl restart corsarr\n' \
        "$APP_DIR" > "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
fi

# --- what to install: a release tag, or the main branch for the dev channel -------------------------
CHANNEL="${CORSARR_CHANNEL:-}"
if [ -z "$CHANNEL" ] && [ -f "$DATA_DIR/config.json" ]; then  # the channel chosen in the web interface
    CHANNEL="$(as_svc python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("UPDATE_CHANNEL", ""))' \
        "$DATA_DIR/config.json" 2>/dev/null || true)"
fi
case "$CHANNEL" in stable|beta|dev) ;; *) CHANNEL=stable ;; esac
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

# --- fetch and verify the target; remember the running version for a rollback -----------------------
UPDATE=0
PREV_COMMIT="" PREV_TAG="" PREV_VERSION="" PREV_SCHEMA=""
if [ -d "$APP_DIR/.git" ]; then
    UPDATE=1
    PREV_COMMIT="$(git -C "$APP_DIR" rev-parse HEAD)"
    PREV_TAG="$(cat "$APP_DIR/.corsarr-version" 2>/dev/null || true)"
    PREV_VERSION="${PREV_TAG:-$(git -C "$APP_DIR" rev-parse --short HEAD)}"
    PREV_VENV="$(readlink "$APP_DIR/.venv" 2>/dev/null || true)"
    say "Fetching $REF …"
    git -C "$APP_DIR" fetch --quiet --tags --force origin "$BRANCH"
else
    say "Downloading code to $APP_DIR ($REF) …"
    git clone --quiet "$REPO" "$APP_DIR"
fi
if [ "$REF" = "$BRANCH" ]; then
    TAG=""
    TARGET="$(git -C "$APP_DIR" rev-parse --verify "origin/$BRANCH^{commit}")"
else
    TAG="$REF"
    TARGET="$(git -C "$APP_DIR" rev-parse --quiet --verify "refs/tags/$REF^{commit}")" || die "Version $REF not found."
fi

if [ "$UPDATE" -eq 1 ]; then
    backup_before_update
    PREV_SCHEMA="$(schema_version)"
fi

# --- prepare the new version next to the running one, then switch -----------------------------------
say "Setting up Python environment for $REF …"
build_venv "$TARGET"

say "Switching code ($APP_DIR) to $REF …"
checkout "$TARGET" "$TAG"
switch_venv "$NEW_VENV"

say "Setting up service …"
install_units
systemctl enable --quiet "$SERVICE"
systemctl enable --now --quiet corsarr-update.path
systemctl restart "$SERVICE"

say "Waiting for startup …"
if healthy; then
    for old in "$APP_DIR"/.venv-*; do  # the previous environment is no longer needed
        [ -d "$old" ] && [ "$(basename "$old")" != "$NEW_VENV" ] && rm -rf "$old"
    done
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

# --- the new version does not start: put the previous one back --------------------------------------
journalctl -u "$SERVICE" -n 30 --no-pager 2>/dev/null || true
HINT=""
if journalctl -u "$SERVICE" -n 50 --no-pager 2>/dev/null | grep -Eq 'mount namespacing|NAMESPACE'; then
    # corsarr.service's sandbox needs mount namespaces, which an LXC container only gets with nesting.
    HINT=" The service's sandbox needs the container feature 'nesting' (Proxmox: Options → Features, or: pct set <id> --features nesting=1)."
fi
if [ "$UPDATE" -ne 1 ]; then
    die "The service does not respond. Details: journalctl -u $SERVICE -n 50 --no-pager$HINT"
fi
say "Version $REF does not start – switching back to $PREV_VERSION …"
systemctl stop "$SERVICE" || true
checkout "$PREV_COMMIT" "$PREV_TAG"
if [ -n "$PREV_VENV" ] && [ -d "$APP_DIR/$PREV_VENV" ]; then
    switch_venv "$PREV_VENV"
fi
rm -rf "${APP_DIR:?}/$NEW_VENV"
if [ -n "$BACKUP" ] && [ -n "$PREV_SCHEMA" ] && [ "$(schema_version)" != "$PREV_SCHEMA" ]; then
    restore_database  # the new version converted the database; the previous one cannot read it
fi
install_units
systemctl restart "$SERVICE" || true
if healthy; then
    die "Update to $REF failed – rolled back to $PREV_VERSION, which is running again. Details above and in: journalctl -u $SERVICE -n 50 --no-pager$HINT"
fi
die "Update to $REF failed and the previous version $PREV_VERSION does not start either. Backup: $BACKUP. Details: journalctl -u $SERVICE -n 50 --no-pager$HINT"
