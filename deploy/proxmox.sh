#!/usr/bin/env bash
# Corsarr – create a Debian LXC container on a Proxmox VE host and install Corsarr inside it.
#
# Run in the Proxmox host shell (Datacenter → <node> → Shell) as root:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/ironiro/corsarr/main/deploy/proxmox.sh)"
#
# Optional settings (set before the command, e.g. CORSARR_CTID=150 bash -c "…"):
#   CORSARR_CTID      container id                 (default: next free id)
#   CORSARR_HOSTNAME  container host name          (default: corsarr)
#   CORSARR_STORAGE   storage for the root disk    (default: first active storage for containers)
#   CORSARR_DISK      disk size in GB              (default: 4)
#   CORSARR_CORES     CPU cores                    (default: 1)
#   CORSARR_RAM       memory in MB                 (default: 512)
#   CORSARR_BRIDGE    network bridge               (default: vmbr0)
#   CORSARR_IP        static address, e.g. 192.168.1.50/24 (default: DHCP)
#   CORSARR_GW        gateway for a static address, e.g. 192.168.1.1
#   CORSARR_BRANCH    branch to install            (default: main)
set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/ironiro/corsarr"
BRANCH="${CORSARR_BRANCH:-main}"
HOSTNAME_CT="${CORSARR_HOSTNAME:-corsarr}"
DISK="${CORSARR_DISK:-4}"
CORES="${CORSARR_CORES:-1}"
RAM="${CORSARR_RAM:-512}"
BRIDGE="${CORSARR_BRIDGE:-vmbr0}"

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
# Run a command in the container with a locale every Debian has. Otherwise the host shell's locale
# (e.g. de_DE.UTF-8, not installed in the fresh container) causes "Setting locale failed" warnings.
ct() { pct exec "$CTID" -- env LANG=C.UTF-8 LC_ALL=C.UTF-8 "$@"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Please run as root in the Proxmox host shell."
if ! command -v pct >/dev/null || ! command -v pveam >/dev/null; then
    die "This is not a Proxmox VE host. Inside a container or VM use deploy/install.sh instead."
fi

CTID="${CORSARR_CTID:-$(pvesh get /cluster/nextid)}"
pct status "$CTID" >/dev/null 2>&1 && die "Container id $CTID is already in use. Set CORSARR_CTID to a free id."

STORAGE="${CORSARR_STORAGE:-$(pvesm status -content rootdir | awk 'NR > 1 && $3 == "active" {print $1; exit}')}"
TMPL_STORAGE="$(pvesm status -content vztmpl | awk 'NR > 1 && $3 == "active" {print $1; exit}')"
[ -n "$STORAGE" ] || die "No active storage for container disks found. Set CORSARR_STORAGE."
[ -n "$TMPL_STORAGE" ] || die "No active storage for container templates found."

if [ -n "${CORSARR_IP:-}" ]; then
    NET="name=eth0,bridge=$BRIDGE,ip=$CORSARR_IP${CORSARR_GW:+,gw=$CORSARR_GW}"
else
    NET="name=eth0,bridge=$BRIDGE,ip=dhcp"
fi

say "Looking for the Debian 12 template …"
pveam update >/dev/null
TEMPLATE="$(pveam available --section system | awk '{print $2}' | grep '^debian-12-standard' | sort -V | tail -n 1)"
[ -n "$TEMPLATE" ] || die "No Debian 12 template found (pveam available --section system)."
if ! pveam list "$TMPL_STORAGE" | grep -q "$TEMPLATE"; then
    say "Downloading $TEMPLATE to $TMPL_STORAGE …"
    pveam download "$TMPL_STORAGE" "$TEMPLATE" >/dev/null
fi

say "Creating container $CTID ($HOSTNAME_CT: $CORES core, $RAM MB, $DISK GB on $STORAGE) …"
pct create "$CTID" "$TMPL_STORAGE:vztmpl/$TEMPLATE" \
    --hostname "$HOSTNAME_CT" --cores "$CORES" --memory "$RAM" --swap "$RAM" \
    --rootfs "$STORAGE:$DISK" --net0 "$NET" \
    --unprivileged 1 --features nesting=1 --onboot 1 --ostype debian >/dev/null
pct start "$CTID"

say "Waiting for the network in the container …"
for _ in $(seq 1 60); do
    ct getent hosts raw.githubusercontent.com >/dev/null 2>&1 && break
    sleep 2
done
ct getent hosts raw.githubusercontent.com >/dev/null 2>&1 \
    || die "Container $CTID has no internet access. Check bridge/IP, then: pct enter $CTID"

say "Enabling root auto-login on the Proxmox console …"
# The container has no root password; the console in the Proxmox UI (reachable only by Proxmox admins)
# logs in automatically, like the Proxmox community scripts do. There is no network login.
# shellcheck disable=SC2016  # single quotes on purpose: the script runs inside the container
ct bash -c '
    mkdir -p /etc/systemd/system/container-getty@1.service.d
    cat > /etc/systemd/system/container-getty@1.service.d/override.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --noclear --keep-baud tty%I 115200,38400,9600 \$TERM
EOF
    systemctl daemon-reload
    systemctl restart container-getty@1.service'

say "Installing Corsarr inside the container …"
ct bash -c "apt-get update -qq && apt-get install -y -qq curl >/dev/null"
ct CORSARR_BRANCH="$BRANCH" \
    bash -c "curl -fsSL --max-time 60 $REPO_RAW/$BRANCH/deploy/install.sh | bash"

IP="$(ct hostname -I | awk '{print $1}')"
echo
say "Done: Corsarr runs in container $CTID ($HOSTNAME_CT)."
echo "    Web interface:  http://${IP:-<container-ip>}:8787/"
echo "    Update:         in the web interface, or: pct exec $CTID -- bash -c \"curl -fsSL $REPO_RAW/main/deploy/install.sh | bash\""
echo "    Console:        Proxmox UI → $CTID → Console (logs in automatically), or: pct enter $CTID"
echo "    Remove:         pct stop $CTID && pct destroy $CTID"
