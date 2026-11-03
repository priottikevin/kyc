import os

os.environ.setdefault("LLM_MODE", "off")
os.environ.setdefault("API_KEYS", "")

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402


@pytest.fixture(scope="session")
def pipeline() -> Pipeline:
    p = Pipeline(get_settings())
    p.warmup()
    return p
