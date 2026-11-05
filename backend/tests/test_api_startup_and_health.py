"""Tests for API startup, health probes, readiness checks, and unconfigured LLM state."""

from __future__ import annotations

import pytest
from app.api.main import create_app
from app.api.settings import ApiSettings
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mock_unconfigured_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensures LLMSettings has no valid API key."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    from app.agents.settings import get_llm_settings

    get_llm_settings.cache_clear()
    yield
    get_llm_settings.cache_clear()


@pytest.mark.anyio
async def test_startup_without_api_key_succeeds(
    mock_unconfigured_llm_settings: None,
) -> None:
    """Verifies that the application boots successfully without any API key."""
    settings = ApiSettings(app_env="development")
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # /health should be 200 OK
        resp_health = await client.get("/health")
        assert resp_health.status_code == 200
        data_health = resp_health.json()
        assert data_health["status"] == "ok"
        assert "timestamp" in data_health

        # /ready should return 503 because LLM is not configured
        resp_ready = await client.get("/ready")
        assert resp_ready.status_code == 503
        data_ready = resp_ready.json()
        assert data_ready["status"] == "not_ready"
        assert data_ready["checks"]["llm_configured"] is False

        # /api/v1/llm/status should show configured=false and NEVER expose keys
        resp_status = await client.get("/api/v1/llm/status")
        assert resp_status.status_code == 200
        data_status = resp_status.json()
        assert data_status["configured"] is False
        assert "api_key" not in data_status
        assert "key" not in data_status
        assert "secret" not in data_status
        assert "circuit_state" in data_status
        assert "budget" in data_status

        # POST /api/v1/analyze should fail fast with 503 LLM_NOT_CONFIGURED
        resp_analyze = await client.post(
            "/api/v1/analyze",
            json={"sql": "SELECT MATNR FROM MARC", "force_refresh": False},
        )
        assert resp_analyze.status_code == 503
        data_err = resp_analyze.json()
        assert data_err["error"]["code"] == "LLM_NOT_CONFIGURED"
        assert "OpenAI API key is not configured" in data_err["error"]["message"]
        assert resp_analyze.headers.get("X-Request-ID") is not None
