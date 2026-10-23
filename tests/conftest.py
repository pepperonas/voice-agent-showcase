from datetime import datetime

import pytest

from app import db

# Montag, 26.10.2026, 07:00 Ortszeit
NOW = datetime(2026, 10, 26, 7, 0)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    db.init(c)
    yield c
    c.close()
