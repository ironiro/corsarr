"""Version check against GitHub and updates triggered from the web interface.

How an update runs depends on how Corsarr was installed:
- service (LXC / install.sh): the app only drops a trigger file. A root systemd path unit
  (deploy/corsarr-update.path) notices it and runs install.sh, which pulls the code, updates
  dependencies and restarts Corsarr. The app itself never gets write access to its own code.
- docker: a container can't replace its own image – the web interface shows the command instead.
- manual: started by hand from a git checkout – the web interface shows `git pull`.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

UPSTREAM = os.environ.get("CORSARR_UPSTREAM", "ironiro/corsarr")  # GitHub owner/repo
BRANCH = os.environ.get("CORSARR_BRANCH", "main")
CODE_ROOT = Path(__file__).resolve().parents[1]
CHECK_TTL = 900  # seconds; GitHub allows 60 unauthenticated requests per hour
MAX_COMMITS = 20

_cache: tuple[float, dict] | None = None


def current_version() -> str | None:
    """Commit the running code was built from: CORSARR_VERSION (Docker) or the git checkout."""
    if os.environ.get("CORSARR_VERSION"):
        return os.environ["CORSARR_VERSION"]
    return _git_head(CODE_ROOT / ".git")


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


def request_update() -> None:
    path = trigger_path()
    if path is None:
        raise RuntimeError("updates from the web interface need the LXC/systemd installation")
    path.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")


def log_tail(data_dir: Path, lines: int = 25) -> str:
    """End of the last update run's output (written by corsarr-update.service)."""
    log = data_dir / "logs" / "update.log"
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


async def check(force: bool = False, client: httpx.AsyncClient | None = None) -> dict:
    """Compare the running version with the branch on GitHub. Cached for CHECK_TTL seconds."""
    global _cache
    if _cache and not force and time.monotonic() - _cache[0] < CHECK_TTL:
        return _cache[1]
    current = current_version()
    own = client is None
    client = client or httpx.AsyncClient(timeout=15, headers={"Accept": "application/vnd.github+json"})
    try:
        if current:
            r = await client.get(f"https://api.github.com/repos/{UPSTREAM}/compare/{current}...{BRANCH}")
        else:
            r = await client.get(f"https://api.github.com/repos/{UPSTREAM}/commits/{BRANCH}")
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as e:
        return {"current": current, "latest": None, "behind": 0, "commits": [], "error": str(e) or type(e).__name__}
    finally:
        if own:
            await client.aclose()

    if current:
        commits = [{"sha": c["sha"], "message": c["commit"]["message"].splitlines()[0],
                    "date": c["commit"]["committer"]["date"]} for c in data.get("commits", [])]
        latest = commits[-1]["sha"] if commits else current
        result = {"current": current, "latest": latest, "behind": int(data.get("ahead_by", 0)),
                  "commits": list(reversed(commits))[:MAX_COMMITS], "error": ""}
    else:
        result = {"current": None, "latest": data.get("sha"), "behind": 0, "commits": [], "error": ""}
    _cache = (time.monotonic(), result)
    return result
