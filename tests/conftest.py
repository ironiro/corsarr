import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corsarr.db import DB  # noqa: E402  (needs the path above)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "t.db")


@pytest.fixture(autouse=True)
def german_texts():
    """Most tests check the German texts; English is the product default (see test_gui)."""
    from corsarr import i18n
    i18n.set_language("de")
    i18n._translated.clear()  # translations of other languages live in memory – none leak between tests
    yield
    i18n.set_language("de")
    i18n._translated.clear()
