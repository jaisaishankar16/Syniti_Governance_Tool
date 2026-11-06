"""Unit tests for the centralized Gateway Client Factory."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from app.agents.client_factory import (
    create_async_client,
    create_sync_client,
    get_base_host,
    validate_base_url,
)
from app.agents.settings import LLMSettings, is_valid_api_key
from pydantic import ValidationError


def test_base_url_used_verbatim():
    """Verifies that base URL is preserved verbatim and no /v1 is appended."""
    url = "https://ai-gateway.corp.internal/v2/openai"
    settings = LLMSettings(
        openai_base_url=url,
        openai_api_key="gateway-secret-token-12345",
    )
    client = create_async_client(settings)
    assert str(client.base_url).rstrip("/") == url.rstrip("/")
    assert not str(client.base_url).endswith("/v1/")


def test_api_key_secret_str_never_leaked():
    """Verifies API key is masked in repr and not leaked to logs."""
    raw_key = "sensitive-corp-gateway-key-xyz"
    settings = LLMSettings(openai_api_key=raw_key)

    assert raw_key not in repr(settings)
    assert raw_key not in str(settings)

    client = create_async_client(settings)
    assert raw_key not in repr(client)


def test_tls_verification_enforced_and_ca_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies TLS verification cannot be disabled and LLM_CA_BUNDLE is applied."""
    ca_file = tmp_path / "corp-ca.pem"
    ca_file.write_text("dummy-cert-content")

    settings = LLMSettings(
        openai_api_key="test-key-12345",
        llm_ca_bundle=ca_file,
    )
    assert settings.llm_ca_bundle == ca_file

    captured_verify = None

    class MockAsyncClient:
        def __init__(self, **kwargs):
            nonlocal captured_verify
            captured_verify = kwargs.get("verify")
            self.base_url = "https://api.openai.com/v1"
            self.is_closed = False

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)
    _ = create_async_client(settings)
    assert captured_verify == str(ca_file)


def test_llm_extra_headers_applied_without_leakage(caplog: pytest.LogCaptureFixture):
    """Verifies custom extra headers are injected into httpx client without leaking in logs."""
    headers = {"X-Corp-Tenant": "MDM-Dept", "X-Routing-Key": "secret-routing-token"}
    settings = LLMSettings(
        openai_api_key="test-key-12345",
        llm_extra_headers=headers,
    )

    with caplog.at_level(logging.DEBUG):
        client = create_sync_client(settings)

    # Check headers applied
    httpx_client = client._client
    assert httpx_client.headers.get("X-Corp-Tenant") == "MDM-Dept"
    assert httpx_client.headers.get("X-Routing-Key") == "secret-routing-token"

    # Check values not logged
    assert "secret-routing-token" not in caplog.text


def test_base_url_validation_matrix():
    """Verifies that invalid base URLs are rejected according to security rules."""
    # Remote HTTP is rejected
    with pytest.raises(ValidationError):
        LLMSettings(openai_base_url="http://remote-server.com/v1")

    # Embedded credentials rejected
    with pytest.raises(ValidationError):
        LLMSettings(openai_base_url=f"https://{'user'}:{'pass'}@gateway.internal")

    # Whitespace rejected
    with pytest.raises(ValidationError):
        LLMSettings(openai_base_url="https://gateway.internal /api")

    # Localhost HTTP is allowed
    settings_local = LLMSettings(openai_base_url="http://localhost:8000/v1")
    assert validate_base_url(settings_local.openai_base_url) == "http://localhost:8000/v1"
    assert get_base_host(settings_local.openai_base_url) == "localhost"


def test_non_sk_key_accepted_and_placeholders_rejected():
    """Accepts arbitrary token formats but rejects placeholders."""
    # Arbitrary enterprise format accepted
    assert is_valid_api_key("eyJuYW1lIjoiZ2F0ZXdheS11c2VyIn0.token") is True
    assert is_valid_api_key("bearer-corp-12345") is True

    # Placeholders rejected
    assert is_valid_api_key("") is False
    assert is_valid_api_key("api_key") is False
    assert is_valid_api_key("your-key-here") is False
    assert is_valid_api_key("changeme") is False
    assert is_valid_api_key("dummy") is False
    assert is_valid_api_key("none") is False
    assert is_valid_api_key("null") is False
    assert is_valid_api_key("sk-...") is False
    assert is_valid_api_key("<ENTER_KEY_HERE>") is False


def test_tracing_refused_when_custom_base_url_set():
    """AGENTS_TRACING=true must be rejected when OPENAI_BASE_URL is set to prevent telemetry leak."""
    with pytest.raises(ValidationError, match="AGENTS_TRACING cannot be enabled"):
        LLMSettings(
            openai_base_url="https://corp.gateway.internal",
            agents_tracing=True,
            openai_api_key="valid-test-key",
        )
