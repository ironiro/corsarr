import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corsarr import config, i18n, updates  # noqa: E402  (needs the path above)
from corsarr.db import DB  # noqa: E402
from corsarr.monitor import health  # noqa: E402

from helpers import ENV  # noqa: E402


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "t.db")


@pytest.fixture(autouse=True)
def german_texts():
    """Most tests check the German texts; English is the product default (see test_gui)."""
    i18n.set_language("de")
    i18n._translated.clear()  # translations of other languages live in memory – none leak between tests
    yield
    i18n.set_language("de")
    i18n._translated.clear()


@pytest.fixture(autouse=True)
def fresh_global_state():
    """The health tiles and the update check's cache are module globals – none leak between tests."""
    health.reset()
    updates._cache.clear()
    yield
    health.reset()
    updates._cache.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A complete configuration from the environment, with the data directory under tmp_path."""
    for f in config.FIELDS:
        monkeypatch.delenv(f.name, raising=False)
    monkeypatch.setenv("CORSARR_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    yield monkeypatch
