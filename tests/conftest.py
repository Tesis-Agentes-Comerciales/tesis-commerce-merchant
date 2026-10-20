import os
from pathlib import Path

import pytest

from commerce_lab.settings import Settings, get_settings

# Only tests/integration opens PostgreSQL; the directory decides the layer (docs/testing.md).
INTEGRATION = Path(__file__).parent / "integration"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip = pytest.mark.skip(reason="requires RUN_DB_INTEGRATION=1 and a disposable PostgreSQL")
    for item in items:
        if item.path.is_relative_to(INTEGRATION):
            item.add_marker(pytest.mark.db)
            if os.environ.get("RUN_DB_INTEGRATION") != "1":
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def ignore_developer_dotenv(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
