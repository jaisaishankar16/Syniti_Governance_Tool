"""Typed application errors with stable codes and sanitized user-facing messages.

Under Project Rules (see docs/PROJECT_RULES.md), whenever the LLM cannot be invoked or fails
to produce a valid response, the application raises a typed error.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"
    LLM_AUTH_FAILED = "LLM_AUTH_FAILED"
    LLM_QUOTA_EXCEEDED = "LLM_QUOTA_EXCEEDED"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_BUDGET_EXCEEDED = "LLM_BUDGET_EXCEEDED"
    SYNTHESIS_FAILED = "SYNTHESIS_FAILED"


class AppError(Exception):
    """Base application exception with a stable code and sanitized message."""

    code: ErrorCode

    def __init__(self, message: str, details: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:
        if self.details:
            return f"[{self.code.value}] {self.message} - {self.details}"
        return f"[{self.code.value}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        return result


class LLMNotConfiguredError(AppError):
    """Raised when no valid LLM API key is configured (empty, placeholder, or missing)."""

    code = ErrorCode.LLM_NOT_CONFIGURED

    def __init__(
        self,
        message: str = (
            "OpenAI API key is not configured or is an invalid placeholder. "
            "Please configure OPENAI_API_KEY in your environment or root .env file. "
            "Run 'python -m app.agents.cli --check-env' to diagnose configuration."
        ),
        details: str | None = None,
    ) -> None:
        super().__init__(message=message, details=details)


class LLMAuthError(AppError):
    """Raised when the LLM provider returns HTTP 401 Unauthorized."""

    code = ErrorCode.LLM_AUTH_FAILED

    def __init__(
        self,
        message: str = "Authentication failed with the LLM provider (HTTP 401). Please verify that your OPENAI_API_KEY is active and valid.",
        details: str | None = None,
    ) -> None:
        super().__init__(message=message, details=details)


class LLMQuotaExceededError(AppError):
    """Raised when the LLM provider returns HTTP 429 for billing / quota exhaustion."""

    code = ErrorCode.LLM_QUOTA_EXCEEDED

    def __init__(
        self,
        message: str = "LLM account quota or billing limit exceeded (HTTP 429). Please check your account usage and credits.",
        details: str | None = None,
    ) -> None:
        super().__init__(message=message, details=details)


class LLMRateLimitedError(AppError):
    """Raised when the LLM provider returns HTTP 429 rate limit and retries are exhausted."""

    code = ErrorCode.LLM_RATE_LIMITED

    def __init__(
        self,
        message: str = "LLM provider rate limit exceeded (HTTP 429). Retries exhausted. Please retry the request later.",
        details: str | None = None,
    ) -> None:
        super().__init__(message=message, details=details)


class LLMUnavailableError(AppError):
    """Raised when the LLM provider is unavailable (network, 5xx, timeout, or circuit breaker open)."""

    code = ErrorCode.LLM_UNAVAILABLE

    def __init__(
        self,
        message: str = "LLM service is currently unavailable (network timeout, provider error, or circuit breaker open).",
        details: str | None = None,
    ) -> None:
        super().__init__(message=message, details=details)


class LLMBudgetExceededError(AppError):
    """Raised when the daily LLM token or cost budget has been spent."""

    code = ErrorCode.LLM_BUDGET_EXCEEDED

    def __init__(
        self,
        message: str = "Daily LLM usage budget limit has been reached for this deployment.",
        details: str | None = None,
    ) -> None:
        super().__init__(message=message, details=details)


class SynthesisFailedError(AppError):
    """Raised when the synthesizer model fails or its output could not be parsed into BusinessIntelligence even after a single retry."""

    code = ErrorCode.SYNTHESIS_FAILED

    def __init__(
        self,
        message: str = "LLM synthesis failed to assemble valid business intelligence after retries.",
        details: str | None = None,
    ) -> None:
        super().__init__(message=message, details=details)


def map_exception_to_app_error(exc: BaseException) -> AppError:
    """Maps low-level or SDK exceptions to typed AppError with sanitized message."""
    import asyncio

    import openai

    if isinstance(exc, AppError):
        return exc

    if isinstance(exc, openai.AuthenticationError):
        return LLMAuthError(details="Provider returned HTTP 401 Unauthorized")

    if isinstance(exc, openai.RateLimitError):
        msg = str(exc).lower()
        if (
            "quota" in msg
            or "billing" in msg
            or "credit" in msg
            or "exceeded your current quota" in msg
        ):
            return LLMQuotaExceededError(
                details="Provider returned 429 indicating quota or billing limits"
            )
        return LLMRateLimitedError(details="Provider returned 429 rate limit")

    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            asyncio.TimeoutError,
        ),
    ):
        return LLMUnavailableError(details=f"Network or provider error: {type(exc).__name__}")

    if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
        return LLMUnavailableError(details=f"Provider 5xx error (status {exc.status_code})")

    return LLMUnavailableError(details=f"Unexpected error: {type(exc).__name__}")
