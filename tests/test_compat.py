"""Configurations and databases from other versions."""
import json
import sqlite3

import pytest

from corsarr import config
from corsarr.db import DB, SCHEMA_VERSION, DatabaseTooNew


def test_database_from_a_newer_version_is_refused(tmp_path):
    path = tmp_path / "corsarr.db"
    DB(path).conn.close()
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(DatabaseTooNew):
        DB(path)


def test_config_file_carries_its_version_and_old_files_still_load(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"JELLYFIN_USER": "Kino"}))  # before versioning
    assert config.read_overrides(tmp_path) == {"JELLYFIN_USER": "Kino"}
    config.write_overrides(tmp_path, {"JELLYFIN_USER": "Kino"})
    data = json.loads((tmp_path / "config.json").read_text())
    assert data[config.VERSION_KEY] == config.CONFIG_VERSION
    data.update({config.VERSION_KEY: config.CONFIG_VERSION + 1, "FIELD_FROM_THE_FUTURE": "x"})
    (tmp_path / "config.json").write_text(json.dumps(data))
    assert config.read_overrides(tmp_path) == {"JELLYFIN_USER": "Kino"}  # newer file: known fields still apply
