"""Unified API error envelope and centralized exception mapping."""

from __future__ import annotations

import logging

from app.errors import (
    AppError,
    ErrorCode,
    LLMBudgetExceededError,
    LLMRateLimitedError,
)
from app.guardrails.models import GuardrailBlocked
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlglot.errors import ParseError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    """Specific field or contextual validation issue."""

    model_config = ConfigDict(extra="forbid")

    field: str | None = Field(default=None, description="Field name where error occurred")
    issue: str = Field(description="Description of the error condition")


class ErrorEnvelopeBody(BaseModel):
    """Standardized error envelope payload."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(description="Stable programmatic error code")
    message: str = Field(description="Sanitized user-facing message")
    request_id: str = Field(description="Tracking identifier for request correlation")
    retry_after_s: int | None = Field(
        default=None, description="Recommended retry delay in seconds if rate-limited"
    )
    details: list[ErrorDetail] = Field(
        default_factory=list, description="Granular issue descriptions"
    )


class ErrorEnvelope(BaseModel):
    """Top-level container for all API error responses."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorEnvelopeBody


# ==============================================================================
# Central Error Code to HTTP Status Mapping
# ==============================================================================

ERROR_CODE_TO_HTTP_STATUS: dict[str, int] = {
    # Guardrail and Parser Errors
    "VALIDATION_ERROR": 422,
    "SQL_PARSE_ERROR": 422,
    "INPUT_REJECTED": 422,
    "PAYLOAD_TOO_LARGE": 413,
    "RATE_LIMITED": status.HTTP_429_TOO_MANY_REQUESTS,
    # Typed LLM Engine Errors
    ErrorCode.LLM_NOT_CONFIGURED.value: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.LLM_AUTH_FAILED.value: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.LLM_QUOTA_EXCEEDED.value: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.LLM_RATE_LIMITED.value: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.LLM_UNAVAILABLE.value: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.LLM_BUDGET_EXCEEDED.value: status.HTTP_429_TOO_MANY_REQUESTS,
    ErrorCode.LLM_MODEL_NOT_FOUND.value: status.HTTP_503_SERVICE_UNAVAILABLE,
    ErrorCode.SYNTHESIS_FAILED.value: status.HTTP_502_BAD_GATEWAY,
    # Generic & HTTP Errors
    "UNAUTHORIZED": status.HTTP_401_UNAUTHORIZED,
    "FORBIDDEN": status.HTTP_403_FORBIDDEN,
    "NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "METHOD_NOT_ALLOWED": status.HTTP_405_METHOD_NOT_ALLOWED,
    "TIMEOUT": status.HTTP_504_GATEWAY_TIMEOUT,
    "INTERNAL_ERROR": status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def get_request_id(request: Request) -> str:
    """Extracts or defaults request ID from request state or header."""
    return getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID", "unknown"
    )


def create_error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    retry_after_s: int | None = None,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Constructs a standardized JSONResponse using ErrorEnvelope."""
    body = ErrorEnvelope(
        error=ErrorEnvelopeBody(
            code=code,
            message=message,
            request_id=request_id,
            retry_after_s=retry_after_s,
            details=details or [],
        )
    )

    resp_headers = headers or {}
    resp_headers["X-Request-ID"] = request_id
    if retry_after_s is not None:
        resp_headers["Retry-After"] = str(retry_after_s)

    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers=resp_headers,
    )


# ==============================================================================
# FastAPI Exception Handlers
# ==============================================================================


async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handles Pydantic input schema validation errors (HTTP 422)."""
    req_id = get_request_id(request)
    details: list[ErrorDetail] = []
    for err in exc.errors():
        loc_str = ".".join(str(p) for p in err.get("loc", []) if p != "body")
        details.append(ErrorDetail(field=loc_str or None, issue=err.get("msg", "Validation error")))

    return create_error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message="Request body or query parameter validation failed.",
        request_id=req_id,
        details=details,
    )


async def sql_parse_exception_handler(request: Request, exc: ParseError) -> JSONResponse:
    """Handles SQL parsing errors from sqlglot (HTTP 422)."""
    req_id = get_request_id(request)
    details: list[ErrorDetail] = []
    line = getattr(exc, "line", None)
    col = getattr(exc, "col", None)
    issue_msg = str(exc).split("\n")[0] if str(exc) else "Failed to parse SQL statement"
    field_info = f"line {line}, col {col}" if line is not None and col is not None else None
    details.append(ErrorDetail(field=field_info, issue=issue_msg))

    return create_error_response(
        status_code=422,
        code="SQL_PARSE_ERROR",
        message="SQL statement contains syntax errors and could not be parsed.",
        request_id=req_id,
        details=details,
    )


async def guardrail_blocked_exception_handler(
    request: Request, exc: GuardrailBlocked
) -> JSONResponse:
    """Handles safety guardrail rejections (HTTP 422).

    Safely lists highest-severity event names without echoing untrusted content.
    """
    req_id = get_request_id(request)
    details: list[ErrorDetail] = []
    if exc.report and exc.report.events:
        for ev in exc.report.events:
            if ev.action == "block":
                details.append(ErrorDetail(field=ev.stage, issue=ev.name))

    return create_error_response(
        status_code=422,
        code="INPUT_REJECTED",
        message="Input query was rejected by enterprise security guardrails.",
        request_id=req_id,
        details=details,
    )


async def app_error_exception_handler(request: Request, exc: AppError) -> JSONResponse:
    """Maps domain AppError hierarchy to typed HTTP responses."""
    req_id = get_request_id(request)
    status_code = ERROR_CODE_TO_HTTP_STATUS.get(
        exc.code.value, status.HTTP_500_INTERNAL_SERVER_ERROR
    )

    retry_after = 60 if isinstance(exc, (LLMRateLimitedError, LLMBudgetExceededError)) else None

    # Sanitize message - never expose internal keys or raw SQL
    details: list[ErrorDetail] = []
    if exc.details and "sk-" not in exc.details:
        details.append(ErrorDetail(issue=exc.details))

    return create_error_response(
        status_code=status_code,
        code=exc.code.value,
        message=exc.message,
        request_id=req_id,
        retry_after_s=retry_after,
        details=details,
    )


async def starlette_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handles standard Starlette / FastAPI HTTPExceptions."""
    req_id = get_request_id(request)
    code_lookup = {
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        413: "PAYLOAD_TOO_LARGE",
        429: "RATE_LIMITED",
        501: "NOT_IMPLEMENTED",
        504: "TIMEOUT",
    }
    code = code_lookup.get(exc.status_code, "HTTP_ERROR")
    msg = exc.detail if isinstance(exc.detail, str) else "HTTP request error"

    return create_error_response(
        status_code=exc.status_code,
        code=code,
        message=msg,
        request_id=req_id,
        headers=getattr(exc, "headers", None),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Fallback handler for unexpected server exceptions (HTTP 500)."""
    req_id = get_request_id(request)
    logger.exception("Unhandled server exception during request %s: %s", req_id, exc)

    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="An unexpected internal server error occurred. Please contact system administration.",
        request_id=req_id,
    )
