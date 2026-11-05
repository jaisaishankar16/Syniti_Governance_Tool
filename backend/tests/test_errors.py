"""Unit tests for typed application errors and exception mapping."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx
import openai
from app.errors import (
    AppError,
    ErrorCode,
    LLMAuthError,
    LLMBudgetExceededError,
    LLMNotConfiguredError,
    LLMQuotaExceededError,
    LLMRateLimitedError,
    LLMUnavailableError,
    SynthesisFailedError,
    map_exception_to_app_error,
)


class TestAppErrors:
    """Verifies typed error codes and conversion from SDK/HTTP errors."""

    def test_error_codes_are_stable(self) -> None:
        assert LLMNotConfiguredError().code == ErrorCode.LLM_NOT_CONFIGURED
        assert LLMAuthError().code == ErrorCode.LLM_AUTH_FAILED
        assert LLMQuotaExceededError().code == ErrorCode.LLM_QUOTA_EXCEEDED
        assert LLMRateLimitedError().code == ErrorCode.LLM_RATE_LIMITED
        assert LLMUnavailableError().code == ErrorCode.LLM_UNAVAILABLE
        assert LLMBudgetExceededError().code == ErrorCode.LLM_BUDGET_EXCEEDED
        assert SynthesisFailedError().code == ErrorCode.SYNTHESIS_FAILED

    def test_app_error_to_dict_and_str(self) -> None:
        err = LLMAuthError(details="Bad API key")
        assert "[LLM_AUTH_FAILED]" in str(err)
        assert "Bad API key" in str(err)
        d = err.to_dict()
        assert d["code"] == "LLM_AUTH_FAILED"
        assert d["details"] == "Bad API key"

    def test_map_already_app_error_passthrough(self) -> None:
        original = LLMBudgetExceededError(details="Limit hit")
        mapped = map_exception_to_app_error(original)
        assert mapped is original

    def test_map_openai_auth_error(self) -> None:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 401
        mock_resp.headers = {}
        exc = openai.AuthenticationError(
            message="Incorrect API key provided",
            response=mock_resp,
            body={"error": {"message": "Incorrect API key"}},
        )
        mapped = map_exception_to_app_error(exc)
        assert isinstance(mapped, LLMAuthError)
        assert mapped.code == ErrorCode.LLM_AUTH_FAILED

    def test_map_openai_quota_error(self) -> None:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 429
        mock_resp.headers = {}
        exc = openai.RateLimitError(
            message="You exceeded your current quota, please check your plan and billing details.",
            response=mock_resp,
            body={"error": {"message": "You exceeded your current quota"}},
        )
        mapped = map_exception_to_app_error(exc)
        assert isinstance(mapped, LLMQuotaExceededError)
        assert mapped.code == ErrorCode.LLM_QUOTA_EXCEEDED

    def test_map_openai_rate_limit_error(self) -> None:
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 429
        mock_resp.headers = {}
        exc = openai.RateLimitError(
            message="Rate limit reached for requests per minute",
            response=mock_resp,
            body={"error": {"message": "Rate limit reached"}},
        )
        mapped = map_exception_to_app_error(exc)
        assert isinstance(mapped, LLMRateLimitedError)
        assert mapped.code == ErrorCode.LLM_RATE_LIMITED

    def test_map_openai_connection_and_timeout_errors(self) -> None:
        conn_exc = openai.APIConnectionError(request=MagicMock())
        mapped_conn = map_exception_to_app_error(conn_exc)
        assert isinstance(mapped_conn, LLMUnavailableError)
        assert mapped_conn.code == ErrorCode.LLM_UNAVAILABLE

        timeout_exc = asyncio.TimeoutError()
        mapped_timeout = map_exception_to_app_error(timeout_exc)
        assert isinstance(mapped_timeout, LLMUnavailableError)
        assert mapped_timeout.code == ErrorCode.LLM_UNAVAILABLE

    def test_map_generic_exception(self) -> None:
        mapped = map_exception_to_app_error(RuntimeError("Unexpected OS crash"))
        assert isinstance(mapped, AppError)
        assert mapped.code == ErrorCode.LLM_UNAVAILABLE
