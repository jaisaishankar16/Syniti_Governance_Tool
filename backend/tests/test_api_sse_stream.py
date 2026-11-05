"""Tests for Server-Sent Events (SSE) streaming, progress updates, heartbeats, and client disconnect shielding."""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from app.api.main import create_app
from app.api.settings import ApiSettings
from app.cache.backends import InMemoryBackend
from app.orchestrator.orchestrator import analyze as real_analyze
from app.orchestrator.progress import ProgressEvent
from httpx import ASGITransport, AsyncClient

from tests.support.fakes import FakeModel


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mock_valid_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-unitTestValidKey1234567890abcdef12345")
    from app.agents.settings import get_llm_settings

    get_llm_settings.cache_clear()
    yield
    get_llm_settings.cache_clear()


@pytest.mark.anyio
async def test_sse_streaming_progress_and_result(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies that progress, result, and done events are received in correct order."""
    settings = ApiSettings(app_env="development", sse_heartbeat_s=1.0)
    backend = InMemoryBackend()
    fake_model = FakeModel()

    async def _test_orchestrator(*args, **kwargs):
        kwargs["model"] = fake_model
        kwargs["cache_backend"] = backend
        return await real_analyze(*args, **kwargs)

    app = create_app(
        settings=settings,
        orchestrator_factory=_test_orchestrator,
        skip_index_check=True,
    )

    sql = "SELECT MARC.MATNR, MARA.MTART FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN dbo.MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/analyze/stream",
            json={"sql": sql, "force_refresh": False},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        body_text = resp.text
        # Assert events appear in the stream
        assert "event: progress" in body_text
        assert "event: result" in body_text
        assert "event: done" in body_text


@pytest.mark.anyio
async def test_sse_event_strict_ordering(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies strict sequential ordering: progress -> progress -> ... -> result -> done."""
    settings = ApiSettings(app_env="development")
    backend = InMemoryBackend()
    fake_model = FakeModel()

    async def _test_orchestrator(*args, **kwargs):
        kwargs["model"] = fake_model
        kwargs["cache_backend"] = backend
        return await real_analyze(*args, **kwargs)

    app = create_app(
        settings=settings,
        orchestrator_factory=_test_orchestrator,
        skip_index_check=True,
    )

    sql = "SELECT MARC.MATNR, MARA.MTART FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN dbo.MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/analyze/stream",
            json={"sql": sql, "force_refresh": False},
        )
        assert resp.status_code == 200

        # Extract event types in order of appearance
        events = re.findall(r"^event:\s*([a-z]+)", resp.text, re.MULTILINE)
        assert len(events) >= 3

        # Must end with result then done
        assert events[-2] == "result"
        assert events[-1] == "done"

        # All events before result must be progress
        for ev in events[:-2]:
            assert ev == "progress"


@pytest.mark.anyio
async def test_sse_heartbeats_emitted_during_long_stream(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies that periodic heartbeat comments (: heartbeat) are emitted during slow stream processing."""
    # Fast heartbeat interval of 100ms
    settings = ApiSettings(app_env="development", sse_heartbeat_s=0.1)

    async def _slow_orchestrator(on_progress=None, **kwargs):
        if on_progress:
            await on_progress(ProgressEvent(stage="guardrails", message="Starting", elapsed_ms=1.0))
        # Sleep for 350ms to guarantee multiple heartbeat ticks
        await asyncio.sleep(0.35)
        fake_model = FakeModel()
        kwargs["model"] = fake_model
        return await real_analyze(sql="SELECT 1 FROM TEST_TABLE", on_progress=on_progress, **kwargs)

    app = create_app(
        settings=settings,
        orchestrator_factory=_slow_orchestrator,
        skip_index_check=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/analyze/stream",
            json={"sql": "SELECT 1 FROM TEST_TABLE", "force_refresh": False},
        )
        assert resp.status_code == 200
        assert ": heartbeat\n\n" in resp.text


@pytest.mark.anyio
async def test_sse_stream_error_after_start(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies that an error occurring during synthesis emits an 'error' event and 'done' event."""
    settings = ApiSettings(app_env="development")

    async def _failing_orchestrator(on_progress=None, **kwargs):
        if on_progress:
            await on_progress(
                ProgressEvent(
                    stage="guardrails",
                    message="Running guardrails",
                    elapsed_ms=5.0,
                )
            )
        raise RuntimeError("Synthesizer model unexpected error")

    app = create_app(
        settings=settings,
        orchestrator_factory=_failing_orchestrator,
        skip_index_check=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/analyze/stream",
            json={"sql": "SELECT 1", "force_refresh": False},
        )
        assert resp.status_code == 200
        body_text = resp.text

        assert "event: progress" in body_text
        assert "event: error" in body_text
        assert "INTERNAL_ERROR" in body_text
        assert "event: done" in body_text
        assert "event: result" not in body_text


@pytest.mark.anyio
async def test_sse_client_disconnect_shields_task_and_fills_cache(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies that when a client disconnects mid-stream, the shielded task runs to completion and populates the cache."""
    settings = ApiSettings(app_env="development")
    backend = InMemoryBackend()
    fake_model = FakeModel()

    execution_completed = asyncio.Event()

    async def _monitored_orchestrator(*args, **kwargs):
        kwargs["model"] = fake_model
        kwargs["cache_backend"] = backend
        # Add slight delay to allow disconnect before completion
        await asyncio.sleep(0.15)
        res = await real_analyze(*args, **kwargs)
        execution_completed.set()
        return res

    app = create_app(
        settings=settings,
        orchestrator_factory=_monitored_orchestrator,
        skip_index_check=True,
    )

    sql = "SELECT MARC.MATNR, MARA.MTART FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN dbo.MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"

    # Start stream and read first chunk, then break to simulate client abrupt disconnect
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
        client.stream(
            "POST",
            "/api/v1/analyze/stream",
            json={"sql": sql, "force_refresh": True},
        ) as response,
    ):
        assert response.status_code == 200
        async for chunk in response.aiter_text():
            if "event: progress" in chunk:
                # Client aborts connection here
                break

    # Wait for the background shielded task to finish
    await asyncio.wait_for(execution_completed.wait(), timeout=5.0)
    assert execution_completed.is_set()

    # Verify that a subsequent request for the same query hits the populated cache
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client2:
        resp_after = await client2.post(
            "/api/v1/analyze",
            json={"sql": sql, "force_refresh": False},
        )
        assert resp_after.status_code == 200
        data_after = resp_after.json()
        assert data_after["meta"]["cache_status"] == "hit_final"


@pytest.mark.anyio
async def test_sse_stream_result_matches_analyze_endpoint(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies that the result payload from SSE stream matches POST /api/v1/analyze."""
    settings = ApiSettings(app_env="development")
    backend = InMemoryBackend()
    fake_model = FakeModel()

    async def _test_orchestrator(*args, **kwargs):
        kwargs["model"] = fake_model
        kwargs["cache_backend"] = backend
        return await real_analyze(*args, **kwargs)

    app = create_app(
        settings=settings,
        orchestrator_factory=_test_orchestrator,
        skip_index_check=True,
    )

    sql = "SELECT MARC.MATNR, MARA.MTART FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN dbo.MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Sync endpoint
        r_sync = await client.post("/api/v1/analyze", json={"sql": sql, "force_refresh": False})
        assert r_sync.status_code == 200
        sync_data = r_sync.json()

        # Stream endpoint
        r_stream = await client.post(
            "/api/v1/analyze/stream", json={"sql": sql, "force_refresh": False}
        )
        assert r_stream.status_code == 200

        # Extract result JSON
        match = re.search(r"event:\s*result\ndata:\s*({[\s\S]*?})\n\n", r_stream.text)
        assert match is not None
        stream_data = json.loads(match.group(1))

        # Core semantic outputs must match
        assert stream_data["business_meaning"] == sync_data["business_meaning"]
        assert stream_data["technical_summary"] == sync_data["technical_summary"]
        assert stream_data["risk_summary"]["level"] == sync_data["risk_summary"]["level"]
        assert (
            stream_data["governance"]["opportunity_query"]
            == sync_data["governance"]["opportunity_query"]
        )
