import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from corsarr.db import DB  # noqa: E402  (needs the path above)


@pytest.fixture
def db(tmp_path):
    return DB(tmp_path / "t.db")
