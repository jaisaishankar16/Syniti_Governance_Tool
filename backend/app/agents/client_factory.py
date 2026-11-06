"""Centralized client factory for OpenAI and gateway-compatible LLM endpoints.

Follows docs/PROJECT_RULES.md:
  - Base URL is used verbatim as configured (never appends /v1, never removes path segments).
  - API key from SecretStr (never logged).
  - Max retries is 0 (retries owned by runner).
  - HTTPX client with trust_env=True for corporate proxy environments.
  - TLS verification is ALWAYS ON (cannot be disabled; optional corporate CA bundle).
  - Tracing is forced OFF at import time.
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from agents import set_tracing_disabled
from openai import AsyncOpenAI, OpenAI

if TYPE_CHECKING:
    from app.agents.settings import LLMSettings

# ------------------------------------------------------------------------------
# Tracing Protection: Force tracing disabled at import time
# ------------------------------------------------------------------------------
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
set_tracing_disabled(True)

logger = logging.getLogger(__name__)


def validate_base_url(url: str | None) -> str | None:
    """Validates the base URL for security and protocol constraints.

    Rules:
      - Whitespace is strictly rejected.
      - Scheme must be https (http only allowed for localhost / 127.0.0.1 / ::1).
      - No embedded credentials (username:password).
      - Returned verbatim without modifying path segments.
    """
    if not url:
        return None
    url_str = str(url).strip()
    if not url_str:
        return None

    if any(c.isspace() for c in url_str):
        raise ValueError(f"OPENAI_BASE_URL contains whitespace: {url_str!r}")

    parsed = urllib.parse.urlsplit(url_str)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"OPENAI_BASE_URL must use https (or http for localhost): {url_str!r}")

    host = (parsed.hostname or "").lower()
    if scheme == "http" and host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise ValueError(
            f"OPENAI_BASE_URL with http scheme is only allowed for localhost, got {host!r}"
        )

    if parsed.username or parsed.password:
        raise ValueError("OPENAI_BASE_URL must not contain embedded user credentials")

    return url_str


def get_base_host(url: str | None) -> str:
    """Extracts host only (no scheme, port or path) for logging and UI diagnostics."""
    if not url:
        return "api.openai.com"
    try:
        parsed = urllib.parse.urlsplit(str(url).strip())
        return parsed.hostname or str(url)
    except Exception:  # noqa: BLE001
        return "unknown"


def _build_tls_verify(ca_bundle: Path | str | None) -> str | bool:
    """Returns the TLS verify parameter. TLS verification is NEVER disabled."""
    if ca_bundle:
        bundle_path = Path(ca_bundle).expanduser().resolve()
        if not bundle_path.is_file():
            raise FileNotFoundError(f"LLM_CA_BUNDLE file not found: {bundle_path}")
        return str(bundle_path)
    return True


def create_async_client(settings: LLMSettings | None = None) -> AsyncOpenAI:
    """Builds an AsyncOpenAI client configured from LLMSettings."""
    from app.agents.settings import get_llm_settings, is_valid_api_key

    s = settings or get_llm_settings()

    base_url = validate_base_url(s.openai_base_url)
    verify_param = _build_tls_verify(s.llm_ca_bundle)

    # Extra headers (values never logged)
    headers: dict[str, str] = {}
    if s.llm_extra_headers:
        headers.update(s.llm_extra_headers)

    http_client = httpx.AsyncClient(
        verify=verify_param,
        trust_env=True,
        headers=headers if headers else None,
        timeout=s.llm_timeout_s,
    )

    api_key_str = (
        s.openai_api_key.get_secret_value()
        if (s.openai_api_key is not None and is_valid_api_key(s.openai_api_key))
        else "NOT_CONFIGURED"
    )

    client_kwargs: dict[str, Any] = {
        "api_key": api_key_str,
        "timeout": s.llm_timeout_s,
        "max_retries": 0,
        "http_client": http_client,
    }
    if base_url:
        client_kwargs["base_url"] = base_url

    return AsyncOpenAI(**client_kwargs)


def create_sync_client(settings: LLMSettings | None = None) -> OpenAI:
    """Builds a synchronous OpenAI client configured from LLMSettings."""
    from app.agents.settings import get_llm_settings, is_valid_api_key

    s = settings or get_llm_settings()

    base_url = validate_base_url(s.openai_base_url)
    verify_param = _build_tls_verify(s.llm_ca_bundle)

    headers: dict[str, str] = {}
    if s.llm_extra_headers:
        headers.update(s.llm_extra_headers)

    http_client = httpx.Client(
        verify=verify_param,
        trust_env=True,
        headers=headers if headers else None,
        timeout=s.llm_timeout_s,
    )

    api_key_str = (
        s.openai_api_key.get_secret_value()
        if (s.openai_api_key is not None and is_valid_api_key(s.openai_api_key))
        else "NOT_CONFIGURED"
    )

    client_kwargs: dict[str, Any] = {
        "api_key": api_key_str,
        "timeout": s.llm_timeout_s,
        "max_retries": 0,
        "http_client": http_client,
    }
    if base_url:
        client_kwargs["base_url"] = base_url

    return OpenAI(**client_kwargs)


def run_smoke_ping(settings: LLMSettings | None = None) -> dict[str, Any]:
    """Sends a single chat completion ping ('Reply with the single word OK.').

    Handles gateway idiosyncrasies:
      - Tries max_tokens=16; falls back to max_completion_tokens=16 if rejected.
      - Tries with temperature; retries without temperature if rejected.
    Returns structured results and classifies failures into standardized error codes.
    """
    from app.agents.runner import classify_provider_error
    from app.agents.settings import get_llm_settings, is_valid_api_key

    s = settings or get_llm_settings()
    host_only = get_base_host(s.openai_base_url)

    if not is_valid_api_key(s.openai_api_key):
        return {
            "success": False,
            "error_code": "LLM_NOT_CONFIGURED",
            "message": "No valid API key configured. Please set OPENAI_API_KEY in your environment or .env.",
            "gateway_host": host_only,
            "model": s.llm_model,
            "latency_ms": 0.0,
        }

    client = create_sync_client(s)
    messages = [{"role": "user", "content": "Reply with the single word OK."}]

    start_time = time.perf_counter()

    def _call(use_max_completion: bool, include_temp: bool) -> Any:
        kwargs: dict[str, Any] = {
            "model": s.llm_model,
            "messages": messages,
        }
        if use_max_completion:
            kwargs["max_completion_tokens"] = 16
        else:
            kwargs["max_tokens"] = 16

        if include_temp:
            kwargs["temperature"] = s.llm_temperature

        return client.chat.completions.create(**kwargs)

    try:
        # Attempt 1: Standard max_tokens with temperature
        use_max_completion = False
        include_temp = True
        try:
            resp = _call(use_max_completion=False, include_temp=True)
        except Exception as exc1:
            err_str = str(exc1).lower()
            # If max_tokens unsupported, switch to max_completion_tokens
            if "max_tokens" in err_str and (
                "unsupported" in err_str or "unknown" in err_str or "not supported" in err_str
            ):
                use_max_completion = True
            # If temperature unsupported, omit it
            if "temperature" in err_str and (
                "unsupported" in err_str or "unknown" in err_str or "not supported" in err_str
            ):
                include_temp = False

            if use_max_completion or not include_temp:
                resp = _call(use_max_completion=use_max_completion, include_temp=include_temp)
            else:
                raise

        latency_ms = (time.perf_counter() - start_time) * 1000
        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "").strip() if choice and choice.message else ""
        usage = {
            "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0) if resp.usage else 0,
            "completion_tokens": getattr(resp.usage, "completion_tokens", 0) if resp.usage else 0,
            "total_tokens": getattr(resp.usage, "total_tokens", 0) if resp.usage else 0,
        }
        return {
            "success": True,
            "model": s.llm_model,
            "gateway_host": host_only,
            "latency_ms": round(latency_ms, 2),
            "response_text": text,
            "usage": usage,
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start_time) * 1000
        typed_err = classify_provider_error(exc)
        return {
            "success": False,
            "error_code": typed_err.code.value,
            "message": typed_err.message,
            "gateway_host": host_only,
            "model": s.llm_model,
            "latency_ms": round(latency_ms, 2),
        }
