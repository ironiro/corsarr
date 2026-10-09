import io
import json
import zipfile

import pyzipper
import pytest

from corsarr import backup
from corsarr.db import SCHEMA_VERSION


def make(files, password=b"backup-secret", encrypt=True):
    buf = io.BytesIO()
    kwargs = {"encryption": pyzipper.WZ_AES} if encrypt else {}
    with pyzipper.AESZipFile(buf, "w", **kwargs) as zf:
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
