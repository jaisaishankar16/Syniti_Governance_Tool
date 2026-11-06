"""Unit tests for gateway-tolerant error classification and retry policies."""

from __future__ import annotations

import httpx
import openai
from app.agents.runner import classify_provider_error, is_transient_error


def _make_openai_error(status_code: int, message: str, body: dict | None = None) -> Exception:
    req = httpx.Request("POST", "https://gateway.corp/chat/completions")
    resp = httpx.Response(status_code, request=req, json=body or {})
    if status_code == 401:
        return openai.AuthenticationError(message=message, response=resp, body=body)
    if status_code == 403:
        return openai.PermissionDeniedError(message=message, response=resp, body=body)
    if status_code == 404:
        return openai.NotFoundError(message=message, response=resp, body=body)
    if status_code == 429:
        return openai.RateLimitError(message=message, response=resp, body=body)
    if status_code == 400:
        return openai.BadRequestError(message=message, response=resp, body=body)
    return openai.InternalServerError(message=message, response=resp, body=body)


def test_auth_error_classification():
    """401 and 403 are classified as LLM_AUTH_FAILED and not retried."""
    err_401 = _make_openai_error(401, "Invalid API key")
    err_403 = _make_openai_error(403, "Forbidden")

    assert classify_provider_error(err_401).code.value == "LLM_AUTH_FAILED"
    assert is_transient_error(err_401) is False

    assert classify_provider_error(err_403).code.value == "LLM_AUTH_FAILED"
    assert is_transient_error(err_403) is False


def test_quota_error_classification():
    """402 or 429 with quota keywords are classified as LLM_QUOTA_EXCEEDED and NOT retried."""
    err_402 = _make_openai_error(402, "Payment Required")
    assert classify_provider_error(err_402).code.value == "LLM_QUOTA_EXCEEDED"
    assert is_transient_error(err_402) is False

    err_429_quota = _make_openai_error(
        429,
        "You exceeded your current quota, please check your plan and billing details.",
        body={"error": {"code": "insufficient_quota"}},
    )
    assert classify_provider_error(err_429_quota).code.value == "LLM_QUOTA_EXCEEDED"
    assert is_transient_error(err_429_quota) is False

    err_429_account = _make_openai_error(429, "Account not active or balance exhausted")
    assert classify_provider_error(err_429_account).code.value == "LLM_QUOTA_EXCEEDED"
    assert is_transient_error(err_429_account) is False


def test_rate_limit_classification():
    """Generic 429 rate limit is classified as LLM_RATE_LIMITED and IS transient."""
    err_429 = _make_openai_error(429, "Requests per minute limit exceeded")
    assert classify_provider_error(err_429).code.value == "LLM_RATE_LIMITED"
    assert is_transient_error(err_429) is True


def test_model_not_found_classification():
    """404 or 400 with model not found is classified as LLM_MODEL_NOT_FOUND and NOT retried."""
    err_404 = _make_openai_error(
        404, "The model `gpt-unknown` does not exist or you do not have access to it."
    )
    assert classify_provider_error(err_404).code.value == "LLM_MODEL_NOT_FOUND"
    assert is_transient_error(err_404) is False

    err_400 = _make_openai_error(400, "Model 'xyz-custom' is not deployed on this gateway endpoint")
    assert classify_provider_error(err_400).code.value == "LLM_MODEL_NOT_FOUND"
    assert is_transient_error(err_400) is False


def test_tls_and_network_error_classification():
    """TLS connection failures classify as LLM_UNAVAILABLE and are retried if transient network failure."""
    req = httpx.Request("POST", "https://gateway.corp/chat/completions")
    conn_err = httpx.ConnectError("TLS certificate validation failure", request=req)
    assert classify_provider_error(conn_err).code.value == "LLM_UNAVAILABLE"
