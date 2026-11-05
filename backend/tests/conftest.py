"""Pytest configuration and test environment isolation fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.api.settings import get_api_settings


@pytest.fixture(autouse=True)
def isolate_activity_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolates activity_data_dir during test runs so tests never pollute production data/activity.jsonl."""
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ACTIVITY_DATA_DIR", str(test_data_dir))
    get_api_settings.cache_clear()
    yield
    get_api_settings.cache_clear()
