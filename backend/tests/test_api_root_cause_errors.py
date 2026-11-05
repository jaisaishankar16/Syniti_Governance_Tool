"""Tests for Phase 5.1 root-cause provider failure classification, retry policy, 60s fail-fast window, and status endpoint."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import openai
import pytest
from app.agents.failure_cache import get_failure_cache
from app.agents.models import AgentDeps, AgentErrorDetails, AgentOutcome, SQLAnalysis
from app.agents.runner import classify_provider_error, is_transient_error, run_agent
from app.agents.settings import LLMSettings
from app.agents.sql_agent import create_sql_agent
from app.api.main import create_app
from app.errors import (
    ErrorCode,
    LLMAuthError,
    LLMQuotaExceededError,
    LLMRateLimitedError,
    LLMUnavailableError,
)
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.store import KnowledgeStore
from app.orchestrator.orchestrator import analyze
from httpx import ASGITransport, AsyncClient

from tests.support.fakes import FakeModel


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_failure_cache() -> None:
    """Ensures failure cache is reset before and after every test."""
    cache = get_failure_cache()
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def mock_valid_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-unitTestValidKey1234567890abcdef12345")
    from app.agents.settings import get_llm_settings

    get_llm_settings.cache_clear()
    yield
    get_llm_settings.cache_clear()


def _make_dummy_response(status_code: int, message: str) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return httpx.Response(
        status_code=status_code,
        json={"error": {"message": message, "type": "invalid_request_error"}},
        request=request,
    )


# ==============================================================================
# 1. Error Classification Unit Tests
# ==============================================================================


def test_classify_provider_error_401() -> None:
    resp = _make_dummy_response(401, "Invalid API key")
    exc = openai.AuthenticationError(message="Incorrect API key", response=resp, body=None)
    err = classify_provider_error(exc)
    assert isinstance(err, LLMAuthError)
    assert err.code == ErrorCode.LLM_AUTH_FAILED
    assert is_transient_error(exc) is False


def test_classify_provider_error_429_quota() -> None:
    resp = _make_dummy_response(
        429, "Your account is not active, please check your billing details"
    )
    exc = openai.RateLimitError(
        message="Your account is not active, please check your billing details",
        response=resp,
        body=None,
    )
    err = classify_provider_error(exc)
    assert isinstance(err, LLMQuotaExceededError)
    assert err.code == ErrorCode.LLM_QUOTA_EXCEEDED
    # MUST NOT be retried
    assert is_transient_error(exc) is False


def test_classify_provider_error_429_rate_limit() -> None:
    resp = _make_dummy_response(429, "Rate limit reached for requests per minute")
    exc = openai.RateLimitError(
        message="Rate limit reached for requests per minute",
        response=resp,
        body=None,
    )
    err = classify_provider_error(exc)
    assert isinstance(err, LLMRateLimitedError)
    assert err.code == ErrorCode.LLM_RATE_LIMITED
    # Rate limit IS transient
    assert is_transient_error(exc) is True


def test_classify_provider_error_500_and_timeout() -> None:
    resp = _make_dummy_response(500, "Internal Server Error")
    exc_500 = openai.InternalServerError(message="Internal Server Error", response=resp, body=None)
    err_500 = classify_provider_error(exc_500)
    assert isinstance(err_500, LLMUnavailableError)
    assert is_transient_error(exc_500) is True

    exc_timeout = openai.APITimeoutError(request=httpx.Request("POST", "http://test"))
    err_timeout = classify_provider_error(exc_timeout)
    assert isinstance(err_timeout, LLMUnavailableError)
    assert is_transient_error(exc_timeout) is True


# ==============================================================================
# 2. Quota Errors are NOT Retried
# ==============================================================================


@pytest.mark.anyio
async def test_quota_error_not_retried(mock_valid_llm_settings: None) -> None:
    """Verifies that an account/quota 429 error fails on attempt 1 without retries."""
    settings = LLMSettings(llm_max_retries=3)
    agent, version = create_sql_agent(FakeModel(), settings)
    deps = AgentDeps(
        store=KnowledgeStore(),
        sap_retriever=SapRetriever(),
        sql_retriever=SqlDocRetriever(),
        settings=settings,
        request_id="test-req-quota-retry",
    )

    resp = _make_dummy_response(
        429, "Your account is not active, please check your billing details"
    )
    quota_exc = openai.RateLimitError(
        message="Your account is not active, please check your billing details",
        response=resp,
        body=None,
    )

    call_count = 0

    async def _failing_runner_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise quota_exc

    with patch("agents.Runner.run", side_effect=_failing_runner_run):
        outcome = await run_agent(agent, "SELECT 1", deps, settings, SQLAnalysis, version)

    assert call_count == 1
    assert outcome.status == "failed"
    assert outcome.error is not None
    assert outcome.error.type == "LLMQuotaExceededError"
    assert outcome.error.error_code == "LLM_QUOTA_EXCEEDED"
    assert outcome.error.retryable is False


# ==============================================================================
# 3. 60-Second Fail-Fast Window
# ==============================================================================


@pytest.mark.anyio
async def test_fail_fast_60s_window_makes_zero_calls(mock_valid_llm_settings: None) -> None:
    """Verifies that within 60s of an auth/quota error, requests fail fast with zero provider calls."""
    cache = get_failure_cache()
    # Record a quota error
    quota_err = LLMQuotaExceededError()
    cache.record_failure(quota_err)

    code, at = cache.get_status()
    assert code == "LLM_QUOTA_EXCEEDED"
    assert at is not None

    app = create_app(skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # POST /api/v1/analyze should fail fast with 503 and zero orchestrator calls
        with patch("app.orchestrator.orchestrator.analyze") as mock_orch:
            resp = await client.post(
                "/api/v1/analyze",
                json={"sql": "SELECT 1", "force_refresh": False},
            )
            assert resp.status_code == 503
            data = resp.json()
            assert data["error"]["code"] == "LLM_QUOTA_EXCEEDED"
            mock_orch.assert_not_called()

        # Check /api/v1/llm/status exposes code and at, but NO message text
        resp_status = await client.get("/api/v1/llm/status")
        assert resp_status.status_code == 200
        st_data = resp_status.json()
        assert st_data["last_error_code"] == "LLM_QUOTA_EXCEEDED"
        assert st_data["last_error_at"] is not None
        assert "message" not in st_data

    # Clearing cache resets the window
    cache.clear()
    code_cleared, at_cleared = cache.get_status()
    assert code_cleared is None
    assert at_cleared is None


# ==============================================================================
# 4. Orchestrator Priority: Auth > Quota > Rate Limit > Unavailable
# ==============================================================================


@pytest.mark.anyio
async def test_both_agents_failing_priority(mock_valid_llm_settings: None) -> None:
    """Verifies that the orchestrator picks the most specific common cause (auth > quota > rate limit > unavailable)."""
    settings = LLMSettings()
    fake_model = FakeModel()

    # Scenario 1: Both fail with quota -> raises LLMQuotaExceededError
    async def _failing_quota(*args, **kwargs):
        return AgentOutcome(
            agent_name="TestAgent",
            status="failed",
            error=AgentErrorDetails(
                type="LLMQuotaExceededError",
                message="Account inactive",
                retryable=False,
                error_code="LLM_QUOTA_EXCEEDED",
            ),
            model="gpt-4o-mini",
            prompt_version="v1",
        )

    with (
        patch("app.orchestrator.orchestrator.run_agent", side_effect=_failing_quota),
        pytest.raises(LLMQuotaExceededError),
    ):
        await analyze("SELECT 1 FROM TEST_TABLE", llm_settings=settings, model=fake_model)

    # Scenario 2: One fails with auth, one with quota -> raises LLMAuthError (auth > quota)
    results = [
        AgentOutcome(
            agent_name="SQLAgent",
            status="failed",
            error=AgentErrorDetails(
                type="LLMAuthError",
                message="Bad key",
                retryable=False,
                error_code="LLM_AUTH_FAILED",
            ),
            model="gpt-4o-mini",
            prompt_version="v1",
        ),
        AgentOutcome(
            agent_name="SAPAgent",
            status="failed",
            error=AgentErrorDetails(
                type="LLMQuotaExceededError",
                message="No quota",
                retryable=False,
                error_code="LLM_QUOTA_EXCEEDED",
            ),
            model="gpt-4o-mini",
            prompt_version="v1",
        ),
    ]

    with (
        patch("app.orchestrator.orchestrator.run_agent", side_effect=results),
        pytest.raises(LLMAuthError),
    ):
        await analyze("SELECT 1 FROM TEST_TABLE", llm_settings=settings, model=fake_model)


# ==============================================================================
# 5. Endpoint Status Mapping Through /analyze and /analyze/stream
# ==============================================================================


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raised_exc, expected_http_status, expected_error_code",
    [
        (LLMAuthError(), 503, "LLM_AUTH_FAILED"),
        (LLMQuotaExceededError(), 503, "LLM_QUOTA_EXCEEDED"),
        (LLMRateLimitedError(), 429, "LLM_RATE_LIMITED"),
        (LLMUnavailableError(), 503, "LLM_UNAVAILABLE"),
    ],
)
async def test_endpoint_status_mapping_sync_and_stream(
    mock_valid_llm_settings: None,
    raised_exc: Exception,
    expected_http_status: int,
    expected_error_code: str,
) -> None:
    """Verifies that /analyze and /analyze/stream return the exact typed error code and HTTP status."""

    async def _failing_orchestrator(*args, **kwargs):
        raise raised_exc

    app = create_app(
        orchestrator_factory=_failing_orchestrator,
        skip_index_check=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # POST /api/v1/analyze
        resp_sync = await client.post(
            "/api/v1/analyze",
            json={"sql": "SELECT 1", "force_refresh": False},
        )
        assert resp_sync.status_code == expected_http_status
        data_sync = resp_sync.json()
        assert data_sync["error"]["code"] == expected_error_code

        # POST /api/v1/analyze/stream
        resp_stream = await client.post(
            "/api/v1/analyze/stream",
            json={"sql": "SELECT 1", "force_refresh": False},
        )
        assert resp_stream.status_code == 200
        body = resp_stream.text
        assert "event: error" in body
        assert expected_error_code in body
        assert "event: done" in body
