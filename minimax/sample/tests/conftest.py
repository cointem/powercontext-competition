import json
from pathlib import Path

import pytest


@pytest.fixture
def orders():
    return json.loads((Path(__file__).parents[1] / "data/orders.json").read_text())
