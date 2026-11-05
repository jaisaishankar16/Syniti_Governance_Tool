"""Tests verifying the error mapping table and envelope structure across all error codes."""

from __future__ import annotations

import pytest
from app.api.main import create_app
from app.api.settings import ApiSettings
from app.errors import (
    ErrorCode,
    LLMAuthError,
    LLMBudgetExceededError,
    LLMNotConfiguredError,
    LLMQuotaExceededError,
    LLMRateLimitedError,
    LLMUnavailableError,
    SynthesisFailedError,
)
from app.guardrails.models import GuardrailBlocked, GuardrailEvent, GuardrailReport
from httpx import ASGITransport, AsyncClient
from sqlglot.errors import ParseError


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
@pytest.mark.parametrize(
    "exception_inst, expected_status, expected_code",
    [
        (LLMNotConfiguredError(), 503, ErrorCode.LLM_NOT_CONFIGURED.value),
        (LLMAuthError(), 503, ErrorCode.LLM_AUTH_FAILED.value),
        (LLMQuotaExceededError(), 503, ErrorCode.LLM_QUOTA_EXCEEDED.value),
        (LLMRateLimitedError(), 429, ErrorCode.LLM_RATE_LIMITED.value),
        (LLMUnavailableError(), 503, ErrorCode.LLM_UNAVAILABLE.value),
        (LLMBudgetExceededError(), 429, ErrorCode.LLM_BUDGET_EXCEEDED.value),
        (SynthesisFailedError(), 502, ErrorCode.SYNTHESIS_FAILED.value),
        (ParseError("Syntax error at line 1 col 15"), 422, "SQL_PARSE_ERROR"),
    ],
)
async def test_error_mapping_table(
    mock_valid_llm_settings: None,
    exception_inst: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    """Verifies that every exception maps strictly to its documented HTTP status and code envelope."""
    settings = ApiSettings(app_env="development")

    async def _failing_orchestrator(*args, **kwargs):
        raise exception_inst

    app = create_app(
        settings=settings,
        orchestrator_factory=_failing_orchestrator,
        skip_index_check=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/analyze",
            json={"sql": "SELECT 1 FROM TEST_TABLE", "force_refresh": False},
        )
        assert resp.status_code == expected_status
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == expected_code
        assert resp.headers.get("X-Request-ID") is not None
        assert data["error"]["request_id"] == resp.headers["X-Request-ID"]

        # Assert no sensitive key substrings leak into response body
        raw_body = resp.text
        assert "sk-" not in raw_body
        assert "SecretStr" not in raw_body


@pytest.mark.anyio
async def test_guardrail_blocked_error_envelope(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies that GuardrailBlocked produces INPUT_REJECTED (HTTP 422) with sanitized details."""
    settings = ApiSettings(app_env="development")
    report = GuardrailReport()
    report.add_event(
        GuardrailEvent(
            name="prompt_injection_detected",
            stage="input",
            action="block",
            severity="critical",
            message="Suspicious delimiter pattern detected",
        )
    )

    async def _failing_orchestrator(*args, **kwargs):
        raise GuardrailBlocked("Security policy block", report=report)

    app = create_app(
        settings=settings,
        orchestrator_factory=_failing_orchestrator,
        skip_index_check=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/analyze",
            json={"sql": "SELECT 1 FROM TEST_TABLE", "force_refresh": False},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "INPUT_REJECTED"
        assert len(data["error"]["details"]) > 0
        assert data["error"]["details"][0]["issue"] == "prompt_injection_detected"


@pytest.mark.anyio
async def test_unexpected_exception_produces_500_internal_error(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies that uncaught internal exceptions produce generic 500 without leaking stack trace."""
    settings = ApiSettings(app_env="development")

    async def _failing_orchestrator(*args, **kwargs):
        raise ZeroDivisionError("division by zero in private code at line 42")

    app = create_app(
        settings=settings,
        orchestrator_factory=_failing_orchestrator,
        skip_index_check=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/analyze",
            json={"sql": "SELECT 1 FROM TEST_TABLE", "force_refresh": False},
        )
        assert resp.status_code == 500
        data = resp.json()
        assert data["error"]["code"] == "INTERNAL_ERROR"
        assert "unexpected internal server error" in data["error"]["message"].lower()
        # Ensure stack trace or code internals are NOT leaked to client
        assert "ZeroDivisionError" not in resp.text
        assert "line 42" not in resp.text
