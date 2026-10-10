import io
import json
import zipfile

import pyzipper
import pytest

from corsarr import backup
from corsarr.db import SCHEMA_VERSION


def make(files, password=b"backup-secret", encrypt=True, compression=pyzipper.ZIP_STORED):
    buf = io.BytesIO()
    kwargs = {"encryption": pyzipper.WZ_AES} if encrypt else {}
    with pyzipper.AESZipFile(buf, "w", compression=compression, **kwargs) as zf:
        if encrypt:
            zf.setpassword(password)
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_backup_from_a_newer_version_is_refused():
    data = make({"manifest.json": json.dumps({"app": "corsarr", "version": "v9.0.0",
                                              "schema_version": SCHEMA_VERSION + 1}),
                 "config.json": "{}"})
    with pytest.raises(backup.BackupError) as e:
        backup.read(data, "backup-secret")
    assert e.value.key == "backup.too_new" and e.value.values["version"] == "v9.0.0"


def test_manifest_with_odd_schema_version_is_not_a_backup():
    data = make({"manifest.json": json.dumps({"app": "corsarr", "schema_version": "lots"}), "config.json": "{}"})
    with pytest.raises(backup.BackupError, match="not_a_backup"):
        backup.read(data, "backup-secret")


def test_zip_bombs_are_refused_before_unpacking(monkeypatch):
    manifest = json.dumps({"app": "corsarr", "schema_version": SCHEMA_VERSION})
    monkeypatch.setattr(backup, "MAX_UNPACKED", 10_000)
    data = make({"manifest.json": manifest, "config.json": "{}", "corsarr.db": b"\0" * 20_000},
                compression=pyzipper.ZIP_DEFLATED)
    assert len(data) < 2_000
    with pytest.raises(backup.BackupError, match="too_large"):
        backup.read(data, "backup-secret")
    monkeypatch.setattr(backup, "MAX_UNPACKED", backup.MAX_SIZE)
    monkeypatch.setattr(backup, "MAX_RATIO", 2)  # 20 kB of zeros pack a few hundredfold
    with pytest.raises(backup.BackupError, match="too_large"):
        backup.read(data, "backup-secret")
    monkeypatch.setattr(backup, "MAX_RATIO", 100_000)
    assert backup.read(data, "backup-secret")[2] == b"\0" * 20_000


def test_restored_settings_are_validated_like_gui_input(tmp_path, caplog):
    settings = {"_config_version": 1, "JELLYFIN_URL": "jf:8096", "JELLYFIN_USER": " Kino ", "TELEGRAM_CHAT_ID": "abc",
                "WEBHOOK_PORT": 9000, "DATA_DIR": "/elsewhere", "UNKNOWN": "x", "LANGUAGE": "de", "EMPTY": ""}
    with caplog.at_level("WARNING", logger="corsarr.backup"):
        clean = backup.clean_settings(settings)
    assert clean == {"JELLYFIN_USER": "Kino", "WEBHOOK_PORT": "9000", "LANGUAGE": "de"}
    assert "JELLYFIN_URL" in caplog.text and "TELEGRAM_CHAT_ID" in caplog.text
    keep = backup.restore(tmp_path, settings, None)
    assert keep.is_dir()
    from corsarr import config
    assert config.read_overrides(tmp_path) == clean


def test_unencrypted_or_foreign_zips_are_refused():
    plain = make({"manifest.json": '{"app": "corsarr"}', "config.json": "{}"}, encrypt=False)
    with pytest.raises(backup.BackupError, match="not_encrypted"):
        backup.read(plain, "")
    other = make({"manifest.json": '{"app": "other"}', "config.json": "{}"})
    with pytest.raises(backup.BackupError, match="not_a_backup"):
        backup.read(other, "backup-secret")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(backup.BackupError, match="not_a_backup"):
        backup.read(buf.getvalue(), "x")
