import asyncio
import os
import time

import httpx

from corsarr import updates

SHA_OLD = "a" * 40
SHA_NEW = "c" * 40


def test_version_from_git_without_running_git(tmp_path):
    git = tmp_path / ".git"
    (git / "refs/heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs/heads/main").write_text(SHA_OLD + "\n")
    assert updates._git_head(git) == SHA_OLD
    (git / "refs/heads/main").unlink()  # after `git gc` the ref lives in packed-refs
    (git / "packed-refs").write_text(f"# pack-refs with: peeled\n{SHA_NEW} refs/heads/main\n")
    assert updates._git_head(git) == SHA_NEW
    (git / "HEAD").write_text(SHA_OLD + "\n")  # detached HEAD
    assert updates._git_head(git) == SHA_OLD


def test_check_lists_new_commits_newest_first(monkeypatch):
    monkeypatch.setattr(updates, "_cache", None)
    monkeypatch.setattr(updates, "current_version", lambda: SHA_OLD)
    seen = []

    def handler(request):
        seen.append(request.url.path)
        commit = lambda sha, msg, day: {"sha": sha, "commit": {"message": msg + "\n\nbody",
                                                               "committer": {"date": f"2026-10-0{day}T10:00:00Z"}}}
        return httpx.Response(200, json={"ahead_by": 2, "commits": [commit("b" * 40, "Fix A", 8),
                                                                   commit(SHA_NEW, "Add B", 9)]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await updates.check(client=client)

    info = asyncio.run(go())
    assert seen == [f"/repos/{updates.UPSTREAM}/compare/{SHA_OLD}...{updates.BRANCH}"]
    assert info["behind"] == 2 and info["latest"] == SHA_NEW
    assert [c["message"] for c in info["commits"]] == ["Add B", "Fix A"]


def test_check_reports_github_errors(monkeypatch):
    monkeypatch.setattr(updates, "_cache", None)
    monkeypatch.setattr(updates, "current_version", lambda: SHA_OLD)

    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"message": "rate limit"}))
        async with httpx.AsyncClient(transport=transport) as client:
            return await updates.check(client=client)

    info = asyncio.run(go())
    assert info["error"] and info["behind"] == 0


def test_running_update_is_detected_from_trigger_and_log(tmp_path, monkeypatch):
    trigger = tmp_path / "update-requested"
    monkeypatch.setenv("CORSARR_UPDATE_TRIGGER", str(trigger))
    (tmp_path / "logs").mkdir()
    log = tmp_path / "logs" / "update.log"
    assert not updates.updating(tmp_path)
    updates.request_update()
    assert trigger.exists() and updates.updating(tmp_path)  # requested, not picked up yet
    trigger.unlink()
    log.write_text("==> Updating code …\n==> Setting up Python environment …\n")
    assert updates.updating(tmp_path)  # install.sh still running
    log.write_text(log.read_text() + "==> Updated and restarted.\n")
    assert not updates.updating(tmp_path)
    log.write_text("==> Updating code …\n")
    old = time.time() - updates.RUN_TIMEOUT - 5
    os.utime(log, (old, old))
    assert not updates.updating(tmp_path)  # stale log: don't spin forever


def test_install_kind(monkeypatch):
    monkeypatch.delenv("CORSARR_VERSION", raising=False)
    monkeypatch.setenv("CORSARR_UPDATE_TRIGGER", "/tmp/x")
    assert updates.install_kind() == "service"
    monkeypatch.delenv("CORSARR_UPDATE_TRIGGER")
    monkeypatch.setenv("CORSARR_VERSION", SHA_OLD)
    assert updates.install_kind() == "docker"
