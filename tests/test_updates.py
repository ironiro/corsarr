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


def test_dev_channel_lists_new_commits_newest_first(monkeypatch):
    monkeypatch.setattr(updates, "_cache", {})
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
            return await updates.check("dev", client=client)

    info = asyncio.run(go())
    assert seen == [f"/repos/{updates.UPSTREAM}/compare/{SHA_OLD}...{updates.BRANCH}"]
    assert info["behind"] == 2 and info["latest"] == SHA_NEW and info["target"] == updates.BRANCH
    assert [c["message"] for c in info["commits"]] == ["Add B", "Fix A"]


def test_check_reports_github_errors(monkeypatch):
    monkeypatch.setattr(updates, "_cache", {})
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
    updates.request_update("v1.2.0")
    assert trigger.read_text().strip() == "v1.2.0" and updates.updating(tmp_path)  # requested, not picked up yet
    trigger.unlink()
    log.write_text("==> Updating code …\n==> Setting up Python environment …\n")
    assert updates.updating(tmp_path)  # install.sh still running
    log.write_text(log.read_text() + "==> Updated and restarted.\n")
    assert not updates.updating(tmp_path)
    log.write_text("==> Updating code …\n")
    old = time.time() - updates.RUN_TIMEOUT - 5
    os.utime(log, (old, old))
    assert not updates.updating(tmp_path)  # stale log: don't spin forever


def test_update_log_lives_where_the_root_service_writes_it(tmp_path, monkeypatch):
    monkeypatch.delenv("CORSARR_UPDATE_TRIGGER", raising=False)
    monkeypatch.delenv("CORSARR_UPDATE_LOG", raising=False)
    assert updates.log_path(tmp_path) == tmp_path / "logs" / "update.log"  # Docker / manual: as before
    assert updates.log_path(None) is None and not updates.updating(None) and updates.log_tail(None) == ""
    log = tmp_path / "var-log-update.log"
    monkeypatch.setenv("CORSARR_UPDATE_LOG", str(log))  # service: root-owned file outside the data dir
    assert updates.log_path(tmp_path) == log
    log.write_text("==> Fetching v1.2.1 …\n==> Setting up Python environment for v1.2.1 …\n")
    assert updates.updating(tmp_path) and updates.log_tail(tmp_path, lines=1) == "==> Setting up Python environment for v1.2.1 …"
    # The installer quotes the service's journal before rolling back; an "Error:" in there is not the end.
    log.write_text(log.read_text() + "Oct 10 12:00:00 corsarr[1]: Error: database is locked\n" + "x\n" * updates.FINAL_LINES)
    assert updates.updating(tmp_path)
    log.write_text(log.read_text() + "Error: Update to v1.2.1 failed – rolled back to v1.2.0, which is running again.\n")
    assert not updates.updating(tmp_path)


def test_install_kind(monkeypatch):
    monkeypatch.delenv("CORSARR_VERSION", raising=False)
    monkeypatch.setenv("CORSARR_UPDATE_TRIGGER", "/tmp/x")
    assert updates.install_kind() == "service"
    monkeypatch.delenv("CORSARR_UPDATE_TRIGGER")
    monkeypatch.setenv("CORSARR_VERSION", SHA_OLD)
    assert updates.install_kind() == "docker"


TAGS = ["v1.3.0-beta.2", "v1.2.1", "v1.3.0-beta.1", "v1.2.0", "nightly", "v1.2"]
RELEASES = [{"tag_name": "v1.2.1", "name": "1.2.1 – fixes", "draft": False, "body": "fix",
             "published_at": "2026-10-09T10:00:00Z"}]


def check_releases(monkeypatch, current, channel):
    monkeypatch.setattr(updates, "_cache", {})
    monkeypatch.setattr(updates, "current_version", lambda: current)

    async def go():
        def handler(request):
            if request.url.path.endswith("/tags"):
                return httpx.Response(200, json=[{"name": t} for t in TAGS])
            return httpx.Response(200, json=RELEASES)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await updates.check(channel, client=client)
    return asyncio.run(go())


def test_stable_channel_ignores_betas_and_odd_tags(monkeypatch):
    info = check_releases(monkeypatch, "v1.2.0", "stable")
    assert info["latest"] == info["target"] == "v1.2.1" and info["behind"] == 1 and not info["downgrade"]
    assert [(r["tag"], r["name"], r["notes"]) for r in info["releases"]] == [("v1.2.1", "1.2.1 – fixes", "fix")]
    assert check_releases(monkeypatch, "v1.2.1", "stable")["target"] is None  # up to date


def test_beta_channel_takes_the_newest_release_of_any_kind(monkeypatch):
    info = check_releases(monkeypatch, "v1.2.0", "beta")
    assert info["target"] == "v1.3.0-beta.2" and info["behind"] == 3
    assert updates.version_key("v1.3.0-beta.2") < updates.version_key("v1.3.0")


def test_back_from_beta_to_stable_is_a_downgrade(monkeypatch):
    info = check_releases(monkeypatch, "v1.3.0-beta.2", "stable")
    assert info["target"] == "v1.2.1" and info["downgrade"] and info["behind"] == 0


def test_dev_commit_is_offered_the_channels_release(monkeypatch):
    info = check_releases(monkeypatch, SHA_OLD, "stable")
    assert info["target"] == "v1.2.1" and info["behind"] == 1 and not info["downgrade"]


def test_version_file_and_targets(tmp_path, monkeypatch):
    monkeypatch.delenv("CORSARR_VERSION", raising=False)
    monkeypatch.setattr(updates, "VERSION_FILE", tmp_path / ".corsarr-version")
    (tmp_path / ".corsarr-version").write_text("v1.2.1\n")
    assert updates.current_version() == "v1.2.1"
    assert updates.valid_target("v1.3.0-beta.2") and updates.valid_target("main")
    assert not updates.valid_target("v1.2") and not updates.valid_target("--upload-pack=x")
    # Only vX.Y.Z and vX.Y.Z-beta.N are releases; other pre-release styles are ignored everywhere.
    for odd in ("v1.2.3-rc.1", "v1.2.3-alpha.1", "v1.2.3-beta", "1.2.3", "v1.2.3.4"):
        assert updates.version_key(odd) is None and not updates.valid_target(odd)
