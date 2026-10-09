"""Backup and restore: database and settings in one AES-encrypted zip file.

The zip can only be opened with the password chosen when it was made – independent of the admin password
of the installation it is restored into. It holds:
- manifest.json  Corsarr version, database and configuration format
- corsarr.db     consistent copy made with SQLite's backup API
- config.json    every setting that differs from its default, including API keys – also values that
                 came from environment variables, so the backup is complete on a new machine
"""
from __future__ import annotations

import io
import json
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

import pyzipper

from . import config, updates
from .db import SCHEMA_VERSION

APP = "corsarr"
MIN_PASSWORD = 8
MAX_SIZE = 200 * 1024 * 1024  # bytes; far more than a household's database
BAD_ZIP = (zipfile.BadZipFile, pyzipper.BadZipFile, ValueError, EOFError)  # pyzipper has its own BadZipFile


class BackupError(Exception):
    """A backup can't be made or restored; the message is meant for the user (i18n key + values)."""

    def __init__(self, key: str, **values):
        super().__init__(key)
        self.key, self.values = key, values


def create(cfg: config.Config, password: str) -> bytes:
    if len(password) < MIN_PASSWORD:
        raise BackupError("backup.password_short", n=MIN_PASSWORD)
    manifest = {"app": APP, "version": updates.current_version() or "", "schema_version": SCHEMA_VERSION,
                "config_version": config.CONFIG_VERSION, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    settings = {f.name: cfg.get(f.name) for f in config.FIELDS
                if f.editable and cfg.get(f.name) and cfg.get(f.name) != f.default}
    settings = {config.VERSION_KEY: config.CONFIG_VERSION, **settings}

    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode())
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("config.json", json.dumps(settings, indent=2, ensure_ascii=False))
        if cfg.db_path.exists():
            zf.writestr("corsarr.db", _db_snapshot(cfg.db_path))
    return buf.getvalue()


def _db_snapshot(path: Path) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "corsarr.db"
        src, dst = sqlite3.connect(path), sqlite3.connect(copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return copy.read_bytes()


def read(data: bytes, password: str) -> tuple[dict, dict, bytes | None]:
    """Check and unpack a backup: (manifest, settings, database). Raises BackupError."""
    if len(data) > MAX_SIZE:
        raise BackupError("backup.too_large")
    try:
        zf = pyzipper.AESZipFile(io.BytesIO(data))
    except BAD_ZIP:
        raise BackupError("backup.not_a_backup") from None
    with zf:
        names = set(zf.namelist())
        if not {"manifest.json", "config.json"} <= names:
            raise BackupError("backup.not_a_backup")
        # Only encrypted backups: a plain zip with the right names is not something Corsarr made.
        if not all(zf.getinfo(n).flag_bits & 0x1 for n in names):
            raise BackupError("backup.not_encrypted")
        zf.setpassword(password.encode())
        try:
            manifest = json.loads(zf.read("manifest.json"))
            settings = json.loads(zf.read("config.json"))
            database = zf.read("corsarr.db") if "corsarr.db" in names else None
        except RuntimeError:  # pyzipper: "Bad password for file"
            raise BackupError("backup.wrong_password") from None
        except BAD_ZIP:
            raise BackupError("backup.not_a_backup") from None
    if not isinstance(manifest, dict) or manifest.get("app") != APP or not isinstance(settings, dict):
        raise BackupError("backup.not_a_backup")
    if int(manifest.get("schema_version", 0)) > SCHEMA_VERSION:
        raise BackupError("backup.too_new", version=manifest.get("version") or "?")
    return manifest, settings, database


def restore(data_dir: Path, settings: dict, database: bytes | None) -> Path:
    """Replace database and settings (the bot must be stopped). Returns where the old data was moved."""
    keep = data_dir / "backups" / f"pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
    keep.mkdir(parents=True, exist_ok=True)
    for name in ("corsarr.db", "corsarr.db-wal", "corsarr.db-shm", config.OVERRIDES_FILE):
        if (data_dir / name).exists():
            shutil.move(str(data_dir / name), keep / name)
    if database is not None:
        tmp = data_dir / "corsarr.db.restoring"
        tmp.write_bytes(database)
        tmp.replace(data_dir / "corsarr.db")
    overrides = config._migrate_overrides(settings)
    config.write_overrides(data_dir, {k: str(v) for k, v in overrides.items()
                                      if k in config.FIELD_BY_NAME and config.FIELD_BY_NAME[k].editable})
    return keep
