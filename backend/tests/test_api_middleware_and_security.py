"""Tests for production middleware: request-id, security headers, CORS, body size limit, and rate limiting."""

from __future__ import annotations

import pytest
from app.api.main import create_app
from app.api.middleware import get_rate_limiter
from app.api.settings import ApiSettings
from httpx import ASGITransport, AsyncClient


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
async def test_request_id_and_security_headers() -> None:
    """Verifies Request-ID generation/passthrough and injection of security headers."""
    settings = ApiSettings(app_env="development")
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Case 1: No incoming X-Request-ID -> server generates UUID
        resp1 = await client.get("/health")
        assert resp1.status_code == 200
        req_id1 = resp1.headers.get("X-Request-ID")
        assert req_id1 is not None
        assert len(req_id1) >= 16

        # Verify security headers
        assert resp1.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp1.headers.get("X-Frame-Options") == "DENY"
        assert resp1.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

        # Case 2: Client passes custom X-Request-ID -> server preserves it
        custom_id = "custom-req-trace-12345"
        resp2 = await client.get("/health", headers={"X-Request-ID": custom_id})
        assert resp2.status_code == 200
        assert resp2.headers.get("X-Request-ID") == custom_id

        # Case 3: API route has Cache-Control no-store
        resp3 = await client.get("/api/v1/examples")
        assert resp3.status_code == 200
        assert "no-store" in resp3.headers.get("Cache-Control", "")


@pytest.mark.anyio
async def test_body_size_limit_rejection() -> None:
    """Verifies that payloads exceeding MAX_BODY_BYTES return HTTP 413 PAYLOAD_TOO_LARGE."""
    # Set tight body limit: 500 bytes
    settings = ApiSettings(app_env="development", max_body_bytes=500)
    app = create_app(settings=settings, skip_index_check=True)

    large_payload = {"sql": "SELECT " + ("A" * 1000) + " FROM MARC"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post("/api/v1/analyze", json=large_payload)
        assert resp.status_code == 413
        data = resp.json()
        assert data["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.anyio
async def test_rate_limiting_bucket_and_retry_after() -> None:
    """Verifies that exceeding rate limits returns HTTP 429 with Retry-After header."""
    # Set tight analyze rate limit: 2 requests per minute
    settings = ApiSettings(
        app_env="development",
        rate_limit_analyze="2/minute",
    )
    limiter = get_rate_limiter()
    limiter.reset_for_test()

    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Request 1: Allowed (returns 422 for missing SQL, but passed rate limiter)
        r1 = await client.post("/api/v1/analyze", json={})
        assert r1.status_code == 422

        # Request 2: Allowed
        r2 = await client.post("/api/v1/analyze", json={})
        assert r2.status_code == 422

        # Request 3: Throttled by rate limiter -> 429
        r3 = await client.post("/api/v1/analyze", json={})
        assert r3.status_code == 429
        data3 = r3.json()
        assert data3["error"]["code"] == "RATE_LIMITED"
        assert r3.headers.get("Retry-After") is not None
        assert int(r3.headers["Retry-After"]) > 0

    limiter.reset_for_test()


@pytest.mark.anyio
async def test_trusted_proxies_ip_resolution() -> None:
    """Verifies X-Forwarded-For is honored only when request arrives from trusted proxy."""
    settings = ApiSettings(
        app_env="development",
        trusted_proxies=["127.0.0.1", "10.0.0.1"],
        rate_limit_analyze="1/minute",
    )
    limiter = get_rate_limiter()
    limiter.reset_for_test()

    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app, client=("10.0.0.1", 12345)),
        base_url="http://testserver",
    ) as client:
        # Arrives from trusted proxy 10.0.0.1 with X-Forwarded-For for 203.0.113.195
        r1 = await client.post(
            "/api/v1/analyze",
            json={},
            headers={"X-Forwarded-For": "203.0.113.195, 10.0.0.1"},
        )
        assert r1.status_code == 422

        # Second request from different forwarded IP should succeed under separate bucket
        r2 = await client.post(
            "/api/v1/analyze",
            json={},
            headers={"X-Forwarded-For": "198.51.100.42, 10.0.0.1"},
        )
        assert r2.status_code == 422

    limiter.reset_for_test()


@pytest.mark.anyio
async def test_cors_allowed_and_denied_origins() -> None:
    """Verifies that requests from allowed origins receive CORS headers, while disallowed origins do not."""
    settings = ApiSettings(
        app_env="development",
        cors_origins=["http://localhost:3000", "https://app.syniti.example.com"],
    )
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Case 1: Allowed origin
        r_allow = await client.options(
            "/api/v1/examples",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r_allow.headers.get("access-control-allow-origin") == "http://localhost:3000"

        # Case 2: Disallowed origin
        r_deny = await client.options(
            "/api/v1/examples",
            headers={
                "Origin": "http://evil-attacker.example.org",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert r_deny.headers.get("access-control-allow-origin") is None


@pytest.mark.anyio
async def test_streamed_chunked_body_over_limit_rejected_413() -> None:
    """Verifies that chunked transfer requests exceeding max_body_bytes return 413 PAYLOAD_TOO_LARGE."""
    settings = ApiSettings(app_env="development", max_body_bytes=1000)
    app = create_app(settings=settings, skip_index_check=True)

    async def _large_stream_generator():
        for _ in range(5):
            yield b'{"chunk": "' + (b"X" * 300) + b'"}'

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/analyze",
            content=_large_stream_generator(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.anyio
async def test_rate_limit_recovery_after_token_refill() -> None:
    """Verifies that after exhausting rate limit tokens, the client recovers once bucket refills."""
    settings = ApiSettings(app_env="development", rate_limit_analyze="2/second")
    limiter = get_rate_limiter()
    limiter.reset_for_test()

    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Exhaust 2 tokens
        r1 = await client.post("/api/v1/analyze", json={})
        assert r1.status_code == 422
        r2 = await client.post("/api/v1/analyze", json={})
        assert r2.status_code == 422

        # 3rd request throttled
        r3 = await client.post("/api/v1/analyze", json={})
        assert r3.status_code == 429

        # Sleep for bucket recovery
        import anyio

        await anyio.sleep(1.1)

        # 4th request succeeds (recovered!)
        r4 = await client.post("/api/v1/analyze", json={})
        assert r4.status_code == 422

    limiter.reset_for_test()


@pytest.mark.anyio
async def test_untrusted_client_ip_ignores_x_forwarded_for() -> None:
    """Verifies that X-Forwarded-For is ignored when request arrives from an untrusted client IP."""
    settings = ApiSettings(
        app_env="development",
        trusted_proxies=["10.0.0.1"],
        rate_limit_analyze="1/minute",
    )
    limiter = get_rate_limiter()
    limiter.reset_for_test()

    app = create_app(settings=settings, skip_index_check=True)

    # Direct client IP is untrusted 198.51.100.5
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("198.51.100.5", 12345)),
        base_url="http://testserver",
    ) as client:
        # First request from 198.51.100.5
        r1 = await client.post(
            "/api/v1/analyze",
            json={},
            headers={"X-Forwarded-For": "1.1.1.1"},
        )
        assert r1.status_code == 422

        # Second request from same direct IP with spoofed different X-Forwarded-For:
        # Must be THROTTLED because direct client IP is not in trusted_proxies!
        r2 = await client.post(
            "/api/v1/analyze",
            json={},
            headers={"X-Forwarded-For": "2.2.2.2"},
        )
        assert r2.status_code == 429
        assert r2.json()["error"]["code"] == "RATE_LIMITED"

    limiter.reset_for_test()
