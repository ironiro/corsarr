"""Version check against GitHub and updates triggered from the web interface.

How an update runs depends on how Corsarr was installed:
- service (LXC / install.sh): the app only drops a trigger file. A root systemd path unit
  (deploy/corsarr-update.path) notices it and runs install.sh, which pulls the code, updates
  dependencies and restarts Corsarr. The app itself never gets write access to its own code.
- docker: a container can't replace its own image – the web interface shows the command instead.
- manual: started by hand from a git checkout – the web interface shows `git pull`.

Release channels: stable = the newest GitHub release that is not a pre-release (tags like v1.2.0),
beta = the newest release including pre-releases (v1.3.0-beta.1), dev = the head of the main branch.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import httpx

UPSTREAM = os.environ.get("CORSARR_UPSTREAM", "ironiro/corsarr")  # GitHub owner/repo
BRANCH = os.environ.get("CORSARR_BRANCH", "main")
CODE_ROOT = Path(__file__).resolve().parents[1]
CHECK_TTL = 900  # seconds; GitHub allows 60 unauthenticated requests per hour
MAX_COMMITS = 20
CHANNELS = ("stable", "beta", "dev")
VERSION_FILE = CODE_ROOT / ".corsarr-version"  # release tag, written by install.sh after checking it out
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")

_cache: dict[str, tuple[float, dict]] = {}


def current_version() -> str | None:
    """Release tag (v1.2.0) or commit the running code was built from.

    CORSARR_VERSION (Docker build), else the tag install.sh recorded, else the git checkout's commit.
    """
    if os.environ.get("CORSARR_VERSION"):
        return os.environ["CORSARR_VERSION"]
    try:
        tag = VERSION_FILE.read_text(encoding="utf-8").strip()
        if TAG.match(tag):
            return tag
    except OSError:
        pass
    return _git_head(CODE_ROOT / ".git")


def version_key(tag: str | None) -> tuple | None:
    """Sortable key for a release tag; a beta sorts before the release of the same number."""
    m = TAG.match(tag or "")
    if not m:
        return None
    major, minor, patch, beta = m.groups()
    return int(major), int(minor), int(patch), int(beta) if beta is not None else float("inf")


def valid_target(target: str) -> bool:
    return target == BRANCH or bool(TAG.match(target))


def _git_head(git: Path) -> str | None:
    """Read HEAD without running git (the service user may not be allowed to run it on this repo)."""
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head or None  # detached HEAD holds the sha itself
        ref = head[5:]
        loose = git / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip()
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
    except OSError:
        pass
    return None


def install_kind() -> str:
    """'service' (can update itself), 'docker' or 'manual'."""
    if os.environ.get("CORSARR_UPDATE_TRIGGER"):
        return "service"
    if Path("/.dockerenv").exists() or os.environ.get("CORSARR_VERSION"):
        return "docker"
    return "manual"


def channel(configured: str) -> str:
    """Channel to check: a Docker image follows the channel of its tag, everything else the setting."""
    image_channel = os.environ.get("CORSARR_CHANNEL", "")
    return image_channel if install_kind() == "docker" and image_channel in CHANNELS else configured


def trigger_path() -> Path | None:
    value = os.environ.get("CORSARR_UPDATE_TRIGGER")
    return Path(value) if value else None


RUN_TIMEOUT = 600  # seconds; an update log untouched for longer counts as finished (or stuck)
FINISHED = ("Updated and restarted.", "Installed.", "Error:")


def updating(data_dir: Path | None = None) -> bool:
    """Requested but not picked up yet, or install.sh still writing its log without a final line."""
    path = trigger_path()
    if path and path.exists():
        return True
    if data_dir is None:
        return False
    log = data_dir / "logs" / "update.log"
    try:
        fresh = time.time() - log.stat().st_mtime < RUN_TIMEOUT
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return fresh and not any(marker in text for marker in FINISHED)


def request_update(target: str) -> None:
    """Ask the root update service to install `target` (a release tag or the dev branch)."""
    path = trigger_path()
    if path is None:
        raise RuntimeError("updates from the web interface need the LXC/systemd installation")
    if not valid_target(target):
        raise ValueError(f"invalid update target: {target!r}")
    path.write_text(target + "\n", encoding="utf-8")


def log_tail(data_dir: Path, lines: int = 25) -> str:
    """End of the last update run's output (written by corsarr-update.service)."""
    log = data_dir / "logs" / "update.log"
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


async def check(channel: str = "stable", force: bool = False, client: httpx.AsyncClient | None = None) -> dict:
    """Compare the running version with the newest one in `channel`. Cached for CHECK_TTL seconds."""
    channel = channel if channel in CHANNELS else "stable"
    cached = _cache.get(channel)
    if cached and not force and time.monotonic() - cached[0] < CHECK_TTL:
        return cached[1]
    current = current_version()
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, headers={"Accept": "application/vnd.github+json"})
    try:
        if channel == "dev":
            result = await _check_branch(client, current)
        else:
            result = await _check_releases(client, current, channel)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return {"channel": channel, "current": current, "latest": None, "target": None, "behind": 0,
                "commits": [], "releases": [], "downgrade": False, "error": str(e) or type(e).__name__}
    finally:
        if own:
            await client.aclose()
    result = {"channel": channel, "current": current, "commits": [], "releases": [], "downgrade": False,
              "error": "", **result}
    _cache[channel] = (time.monotonic(), result)
    return result


async def _check_branch(client: httpx.AsyncClient, current: str | None) -> dict:
    """dev channel: commits on the branch since the running commit."""
    if current and not TAG.match(current):
        r = await client.get(f"https://api.github.com/repos/{UPSTREAM}/compare/{current}...{BRANCH}")
        r.raise_for_status()
        data = r.json()
        commits = [{"sha": c["sha"], "message": c["commit"]["message"].splitlines()[0],
                    "date": c["commit"]["committer"]["date"]} for c in data.get("commits", [])]
        latest = commits[-1]["sha"] if commits else current
        return {"latest": latest, "target": BRANCH, "behind": int(data.get("ahead_by", 0)),
                "commits": list(reversed(commits))[:MAX_COMMITS]}
    # Running a release (or unknown): switching to dev always means "the newest commit".
    r = await client.get(f"https://api.github.com/repos/{UPSTREAM}/commits/{BRANCH}")
    r.raise_for_status()
    sha = r.json().get("sha")
    return {"latest": sha, "target": BRANCH, "behind": 1 if sha and sha != current else 0}


async def _check_releases(client: httpx.AsyncClient, current: str | None, channel: str) -> dict:
    """stable/beta channel: the newest matching GitHub release, plus the ones in between."""
    r = await client.get(f"https://api.github.com/repos/{UPSTREAM}/releases", params={"per_page": 30})
    r.raise_for_status()
    releases = [
        {"tag": rel["tag_name"], "name": rel.get("name") or rel["tag_name"], "prerelease": rel["prerelease"],
         "date": rel.get("published_at") or "", "notes": (rel.get("body") or "")[:2000], "url": rel.get("html_url", "")}
        for rel in r.json()
        if not rel.get("draft") and version_key(rel.get("tag_name"))
        and (channel == "beta" or not rel["prerelease"])
    ]
    releases.sort(key=lambda rel: version_key(rel["tag"]), reverse=True)
    if not releases:
        return {"latest": None, "target": None, "behind": 0}
    latest = releases[0]["tag"]
    have = version_key(current)
    if have is None:  # running a dev commit: offer the channel's newest release
        newer = releases[:1] if current != latest else []
    else:
        newer = [rel for rel in releases if version_key(rel["tag"]) > have]
    return {"latest": latest, "target": latest if latest != current else None, "behind": len(newer),
            "releases": newer[:10],
            # e.g. switched from beta back to stable: the newest stable is older than what runs now
            "downgrade": have is not None and version_key(latest) < have}
