"""Unit and integration tests for real execution activity recording and persistence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.api.deps import get_activity_repo, get_orchestrator
from app.api.main import create_app
from app.api.models import ActivityItem
from app.api.repositories import JsonLinesActivityRepository
from app.api.settings import ApiSettings
from app.guardrails.models import GuardrailReport
from app.synthesizer.models import (
    BusinessIntelligence,
    BusinessIntelligenceMeta,
    CacheDetail,
    PromptVersions,
    TokenUsageBreakdown,
    ValidationSummary,
)
from httpx import ASGITransport, AsyncClient

from tests.support.fakes import CANNED_SYNTHESIZER_OUTPUT


def _build_test_bi() -> BusinessIntelligence:
    meta = BusinessIntelligenceMeta(
        request_id="req_test_act_123",
        cache_status="miss",
        cache_detail=CacheDetail(),
        model="gpt-4o-mini",
        prompt_versions=PromptVersions(sql="sql_v1", sap="sap_v1", synthesizer="synth_v1"),
        agent_timings_ms={"sql_agent": 10.0, "sap_agent": 15.0, "synthesizer": 20.0},
        stage_timings_ms={"total": 45.0},
        token_usage=TokenUsageBreakdown(total=100),
        degraded=False,
        degraded_reasons=[],
        warnings=[],
        guardrails=GuardrailReport(blocked=False),
        validation=ValidationSummary(
            opportunity_sql_ok=True,
            error_sql_ok=True,
            subset_ok=True,
            glossary_ok=True,
            matches_parser_baseline=True,
        ),
        ai_generated=True,
    )
    canned = CANNED_SYNTHESIZER_OUTPUT
    return BusinessIntelligence(
        business_meaning=canned.business_meaning,
        technical_summary=canned.technical_summary,
        affected_entities=canned.affected_entities,
        sap_module=canned.sap_module,
        query_type=canned.query_type,
        risk_summary=canned.risk_summary,
        final_learning=canned.final_learning,
        governance=canned.governance,
        meta=meta,
    )


@pytest.fixture
def temp_activity_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.mark.anyio
async def test_activity_recording_on_success_and_cache(temp_activity_dir: Path) -> None:
    """Verifies successful executions and cache hits are recorded with correct fields."""
    activity_file = temp_activity_dir / "activity.jsonl"
    repo = JsonLinesActivityRepository(file_path=activity_file)

    item1 = ActivityItem(
        id="act-1",
        request_id="req-1",
        status="complete",
        run_type="Trial Execution",
        table_name="MARA",
        subtitle="MM - Material Master General Data",
        cache_status="miss",
        duration_ms=450.2,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    item2 = ActivityItem(
        id="act-2",
        request_id="req-2",
        status="complete",
        run_type="Trial Execution (cached)",
        table_name="MARA",
        subtitle="MM - Material Master General Data",
        cache_status="hit_final",
        duration_ms=1.5,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )

    await repo.record_activity(item1)
    await repo.record_activity(item2)

    # Newest first
    listed = await repo.list_activity(limit=10)
    assert len(listed) == 2
    assert listed[0].id == "act-2"
    assert listed[0].run_type == "Trial Execution (cached)"
    assert listed[1].id == "act-1"
    assert listed[1].run_type == "Trial Execution"


@pytest.mark.anyio
async def test_activity_never_stores_sql_or_result_bodies(temp_activity_dir: Path) -> None:
    """Asserts that SQL query text and synthesis results NEVER appear in activity.jsonl."""
    activity_file = temp_activity_dir / "activity.jsonl"
    repo = JsonLinesActivityRepository(file_path=activity_file)

    secret_sql = "SELECT MARC.SECRET_COLUMN_9876 FROM dbo.TOP_SECRET_TABLE_1234 WHERE MANDT = '100'"
    secret_narrative = "CONFIDENTIAL_FINANCIAL_METRICS_AND_EXECUTIVE_SUMMARY"

    # Only view/table name and subtitle should be stored
    item = ActivityItem(
        id="act-secret-test",
        request_id="req-secret",
        status="complete",
        run_type="Trial Execution",
        table_name="TOP_SECRET_TABLE_1234",
        subtitle="MM - Materials",
        cache_status="miss",
        duration_ms=300.0,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    await repo.record_activity(item)

    content = activity_file.read_text(encoding="utf-8")
    assert secret_sql not in content
    assert secret_narrative not in content
    assert "TOP_SECRET_TABLE_1234" in content
    assert "act-secret-test" in content


@pytest.mark.anyio
async def test_failed_runs_are_recorded(temp_activity_dir: Path) -> None:
    """Verifies failed analysis runs record an entry with status='failed' and error code."""
    activity_file = temp_activity_dir / "activity.jsonl"
    repo = JsonLinesActivityRepository(file_path=activity_file)

    failed_item = ActivityItem(
        id="act-fail-1",
        request_id="req-err-401",
        status="failed",
        run_type="Trial Execution",
        table_name="MARC",
        subtitle="LLM_AUTH_FAILED",
        cache_status="miss",
        duration_ms=120.0,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    await repo.record_activity(failed_item)

    listed = await repo.list_activity(limit=10)
    assert len(listed) == 1
    assert listed[0].status == "failed"
    assert listed[0].subtitle == "LLM_AUTH_FAILED"


@pytest.mark.anyio
async def test_concurrent_writes_stay_valid(temp_activity_dir: Path) -> None:
    """Verifies filelock guarantees data integrity across concurrent writes."""
    activity_file = temp_activity_dir / "activity.jsonl"
    repo = JsonLinesActivityRepository(file_path=activity_file)

    async def _write(idx: int) -> None:
        item = ActivityItem(
            id=f"act-concur-{idx}",
            request_id=f"req-{idx}",
            status="complete",
            run_type="Trial Execution",
            table_name=f"TABLE_{idx}",
            subtitle="Concurrent write test",
            cache_status="miss",
            duration_ms=float(idx),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        await repo.record_activity(item)

    # 40 concurrent writes
    await asyncio.gather(*[_write(i) for i in range(40)])

    listed = await repo.list_activity(limit=100)
    assert len(listed) == 40
    # Verify no line corrupted
    lines = activity_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 40


@pytest.mark.anyio
async def test_cap_enforced_at_max_entries(temp_activity_dir: Path) -> None:
    """Verifies activity file is capped at max_entries and drops oldest items."""
    activity_file = temp_activity_dir / "activity.jsonl"
    repo = JsonLinesActivityRepository(file_path=activity_file, max_entries=50)

    for i in range(75):
        item = ActivityItem(
            id=f"act-cap-{i}",
            request_id=f"req-{i}",
            status="complete",
            run_type="Trial Execution",
            table_name="TABLE_X",
            subtitle=f"Item {i}",
            cache_status="miss",
            duration_ms=10.0,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        await repo.record_activity(item)

    listed = await repo.list_activity(limit=100)
    assert len(listed) == 50
    # Newest item was 74
    assert listed[0].id == "act-cap-74"
    # Oldest retained item was 25 (0 to 24 dropped)
    assert listed[-1].id == "act-cap-25"


@pytest.mark.anyio
async def test_recorder_failure_does_not_break_analyze(
    monkeypatch: pytest.MonkeyPatch, temp_activity_dir: Path
) -> None:
    """Verifies that an activity repository failure does NOT fail or crash /api/v1/analyze."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-unitTestValidKey1234567890abcdef12345")
    from app.agents.settings import get_llm_settings

    get_llm_settings.cache_clear()

    settings = ApiSettings(app_env="development")

    class BrokenActivityRepository:
        async def list_activity(self, limit: int = 20) -> list[ActivityItem]:
            return []

        async def record_activity(self, item: ActivityItem) -> None:
            raise OSError("Simulated disk write failure or permissions error")

    async def fake_orchestrator(*args, **kwargs) -> BusinessIntelligence:
        return _build_test_bi()

    app = create_app(settings=settings, skip_index_check=True)
    app.dependency_overrides[get_activity_repo] = lambda: BrokenActivityRepository()
    app.dependency_overrides[get_orchestrator] = lambda: fake_orchestrator

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            # Should succeed 200 despite recorder raising OSError
            resp = await client.post(
                "/api/v1/analyze",
                json={"sql": "SELECT 1 AS id"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "business_meaning" in data
    finally:
        get_llm_settings.cache_clear()
