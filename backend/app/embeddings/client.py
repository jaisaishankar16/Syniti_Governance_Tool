"""Client factory for OpenAI and gateway-compatible embeddings endpoints."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx
from openai import AsyncOpenAI, OpenAI

from app.agents.client_factory import _build_tls_verify, validate_base_url
from app.agents.settings import get_llm_settings, is_valid_api_key

if TYPE_CHECKING:
    from app.embeddings.settings import EmbeddingSettings

logger = logging.getLogger("app.embeddings.client")


def create_embedding_async_client(
    settings: EmbeddingSettings | None = None,
) -> AsyncOpenAI:
    """Builds an AsyncOpenAI client configured for embeddings.

    Reuses Phase 6.3 rules:
      - base URL used verbatim (no /v1 appended).
      - API key from SecretStr (never logged).
      - httpx client with trust_env=True.
      - TLS verification always ON.
      - max_retries=0 (our code owns retries).
    """
    from app.embeddings.settings import get_embedding_settings

    s = settings or get_embedding_settings()
    llm_s = get_llm_settings()

    base_url = validate_base_url(s.effective_base_url)
    verify_param = _build_tls_verify(llm_s.llm_ca_bundle)

    headers: dict[str, str] = {}
    if llm_s.llm_extra_headers:
        headers.update(llm_s.llm_extra_headers)

    http_client = httpx.AsyncClient(
        verify=verify_param,
        trust_env=True,
        headers=headers if headers else None,
        timeout=s.embedding_timeout_s,
    )

    api_key_str = (
        s.effective_api_key.get_secret_value()
        if (s.effective_api_key is not None and is_valid_api_key(s.effective_api_key))
        else "NOT_CONFIGURED"
    )

    client_kwargs: dict[str, Any] = {
        "api_key": api_key_str,
        "timeout": s.embedding_timeout_s,
        "max_retries": 0,
        "http_client": http_client,
    }
    if base_url:
        client_kwargs["base_url"] = base_url

    return AsyncOpenAI(**client_kwargs)


def create_embedding_sync_client(
    settings: EmbeddingSettings | None = None,
) -> OpenAI:
    """Builds a synchronous OpenAI client configured for embeddings."""
    from app.embeddings.settings import get_embedding_settings

    s = settings or get_embedding_settings()
    llm_s = get_llm_settings()

    base_url = validate_base_url(s.effective_base_url)
    verify_param = _build_tls_verify(llm_s.llm_ca_bundle)

    headers: dict[str, str] = {}
    if llm_s.llm_extra_headers:
        headers.update(llm_s.llm_extra_headers)

    http_client = httpx.Client(
        verify=verify_param,
        trust_env=True,
        headers=headers if headers else None,
        timeout=s.embedding_timeout_s,
    )

    api_key_str = (
        s.effective_api_key.get_secret_value()
        if (s.effective_api_key is not None and is_valid_api_key(s.effective_api_key))
        else "NOT_CONFIGURED"
    )

    client_kwargs: dict[str, Any] = {
        "api_key": api_key_str,
        "timeout": s.embedding_timeout_s,
        "max_retries": 0,
        "http_client": http_client,
    }
    if base_url:
        client_kwargs["base_url"] = base_url

    return OpenAI(**client_kwargs)
