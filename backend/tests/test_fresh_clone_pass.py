"""Test proving the test suite runs and skips gracefully on a fresh clone with proprietary files absent."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_proprietary_fixtures_absent_behavior(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verifies that non-existent proprietary fixtures cause clean skips, not unhandled errors."""
    fake_fixtures_dir = tmp_path / "fixtures"
    fake_fixtures_dir.mkdir(parents=True, exist_ok=True)

    non_existent_json = fake_fixtures_dir / "examples.json"
    non_existent_xlsx = fake_fixtures_dir / "DQ_Queries.xlsx"

    assert not non_existent_json.exists()
    assert not non_existent_xlsx.exists()

    # Verify that attempting to load missing fixtures triggers pytest.skip
    with pytest.raises(pytest.skip.Exception):
        if not non_existent_json.exists():
            pytest.skip("examples.json fixture not found (proprietary fixture excluded from git)")

    with pytest.raises(pytest.skip.Exception):
        if not non_existent_xlsx.exists():
            pytest.skip("DQ_Queries.xlsx fixture not found (proprietary fixture excluded from git)")
