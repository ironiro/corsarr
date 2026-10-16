#!/usr/bin/env bash
# Update a Docker Compose installation of Corsarr when someone presses "Update now" in the web interface.
#
# A container can't replace its own image. With CORSARR_UPDATE_TRIGGER set in the container, the app instead
# writes the version to install (a release tag such as v1.2.0, or "main" for the dev channel) into a file in
# its data directory. corsarr-docker-update.path on the Docker host notices it and runs this script as root:
#
#   1. pull the image for that version (v1.2.0 -> :1.2.0, main -> :edge)
#   2. stop Corsarr and copy the database aside (the new version may migrate it)
#   3. start the container with the new tag (CORSARR_TAG in the stack's .env, see docker-compose.yml)
#   4. wait until Docker reports it healthy – otherwise put back the old tag and database
#
# The output goes to a log the web interface shows (mounted read-only into the container). Its last line is
# one of the markers corsarr/updates.py looks for: "Updated and restarted." or "Error: …".
#
# Settings (environment, e.g. in corsarr-docker-update.service):
#   CORSARR_STACK     directory with docker-compose.yml          (default /opt/stacks/corsarr)
#   CORSARR_DATA      the data directory mounted as /data          (default $CORSARR_STACK/data)
#   CORSARR_IMAGE     image without tag                          (default ghcr.io/ironiro/corsarr)
#   CORSARR_SERVICE   service/container name                     (default corsarr)
#   CORSARR_HEALTH_WAIT  seconds to wait for "healthy"           (default 180)
set -uo pipefail

STACK=${CORSARR_STACK:-/opt/stacks/corsarr}
DATA=${CORSARR_DATA:-$STACK/data}
IMAGE=${CORSARR_IMAGE:-ghcr.io/ironiro/corsarr}
SERVICE=${CORSARR_SERVICE:-corsarr}
HEALTH_WAIT=${CORSARR_HEALTH_WAIT:-180}
TRIGGER=$DATA/update-request
LOG=$STACK/update.log
ENV_FILE=$STACK/.env
BACKUPS=$STACK/update-backups
KEEP_BACKUPS=3

exec >"$LOG" 2>&1
chmod 0644 "$LOG"
echo "==> $(date '+%Y-%m-%d %H:%M:%S') Update requested in the web interface"

target=$(head -c 64 "$TRIGGER" 2>/dev/null | tr -d '[:space:]')
rm -f "$TRIGGER"
if [[ $target =~ ^v([0-9]+\.[0-9]+\.[0-9]+(-beta\.[0-9]+)?)$ ]]; then
    tag=${BASH_REMATCH[1]}
elif [[ $target == main ]]; then
    tag=edge
else
    echo "Error: invalid update target '${target}'"
    exit 1
fi

cd "$STACK" || { echo "Error: $STACK not found"; exit 1; }
old=$(sed -n 's/^CORSARR_TAG=//p' "$ENV_FILE" 2>/dev/null | tail -n 1)
old=${old:-latest}

set_tag() {
    touch "$ENV_FILE"
    { grep -v '^CORSARR_TAG=' "$ENV_FILE"; echo "CORSARR_TAG=$1"; } > "$ENV_FILE.new" && mv "$ENV_FILE.new" "$ENV_FILE"
}

health() {
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$SERVICE" 2>/dev/null
}

wait_healthy() {
    local waited=0 state
    while (( waited < HEALTH_WAIT )); do
        state=$(health)
        case $state in
            healthy) return 0 ;;
            unhealthy|exited|dead) echo "    container is $state"; return 1 ;;
        esac
        sleep 5
        (( waited += 5 ))
    done
    echo "    not healthy after ${HEALTH_WAIT}s (last state: ${state:-unknown})"
    return 1
}

echo "==> Pulling $IMAGE:$tag …"
if ! docker pull -q "$IMAGE:$tag" </dev/null; then
    echo "Error: could not pull $IMAGE:$tag – nothing was changed"
    exit 1
fi

echo "==> Stopping Corsarr and backing up the database …"
docker compose stop "$SERVICE" </dev/null
backup=$BACKUPS/pre-update-$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup"
cp -a "$DATA"/corsarr.db* "$backup"/ 2>/dev/null
# Only the newest few copies are kept
ls -1dt "$BACKUPS"/pre-update-* 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -rf

echo "==> Starting $IMAGE:$tag (was :$old) …"
set_tag "$tag"
docker compose up -d "$SERVICE" </dev/null
if wait_healthy; then
    echo "==> Running $target"
    echo "Updated and restarted."
    exit 0
fi

echo "==> The new version did not start properly – going back to :$old …"
docker compose logs --tail 30 "$SERVICE" </dev/null | sed 's/^/    /'
docker compose stop "$SERVICE" </dev/null
rm -f "$DATA"/corsarr.db-wal "$DATA"/corsarr.db-shm
cp -a "$backup"/corsarr.db* "$DATA"/ 2>/dev/null
set_tag "$old"
docker compose up -d "$SERVICE" </dev/null
wait_healthy || echo "    the old version does not come up either – check: docker compose logs $SERVICE"
echo "Error: $target did not start; rolled back to :$old (database from before the update restored)"
exit 1
