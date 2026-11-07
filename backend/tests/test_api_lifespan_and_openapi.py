"""Tests for FastAPI lifespan events, vector index startup validation, graceful shutdown, and docs flags."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from app.api.main import create_app
from app.api.settings import ApiSettings
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_startup_succeeds_when_vector_index_is_stale() -> None:
    """Verifies that if a vector index is stale/missing, startup succeeds without raising RuntimeError."""
    settings = ApiSettings(app_env="production")
    app = create_app(settings=settings, skip_index_check=False)

    with patch("app.api.main.VectorIndex") as mock_vindex_cls:
        mock_idx = MagicMock()
        mock_idx.index_name = "sap_docs"
        mock_idx.is_stale.return_value = (True, "manifest missing")
        mock_vindex_cls.return_value = mock_idx

        async with app.router.lifespan_context(app):
            pass


@pytest.mark.anyio
async def test_graceful_shutdown_waits_for_active_analyses() -> None:
    """Verifies that shutdown waits for in-flight active analyses until count drops to zero."""
    settings = ApiSettings(app_env="development", shutdown_grace_s=2.0)
    app = create_app(settings=settings, skip_index_check=True)

    shutdown_waited = False

    async with app.router.lifespan_context(app):
        # Simulate an in-flight analysis
        app.state.active_analyses = 1

        async def _finish_analysis_after_delay() -> None:
            nonlocal shutdown_waited
            await asyncio.sleep(0.6)
            app.state.active_analyses = 0
            shutdown_waited = True

        asyncio.create_task(_finish_analysis_after_delay())

    # Exiting context triggers shutdown
    assert shutdown_waited is True
    assert app.state.active_analyses == 0
    assert app.state.accepting_analyses is False


@pytest.mark.anyio
async def test_production_docs_disabled_flag() -> None:
    """Verifies that when docs_enabled=False in production, /docs, /redoc, /openapi.json return 404."""
    settings = ApiSettings(app_env="production", docs_enabled=False)
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        r_docs = await client.get("/docs")
        assert r_docs.status_code == 404

        r_redoc = await client.get("/redoc")
        assert r_redoc.status_code == 404

        r_openapi = await client.get("/openapi.json")
        assert r_openapi.status_code == 404


@pytest.mark.anyio
async def test_docs_enabled_in_development() -> None:
    """Verifies that when docs_enabled=True, /docs and /openapi.json are accessible."""
    settings = ApiSettings(app_env="development", docs_enabled=True)
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        r_docs = await client.get("/docs")
        assert r_docs.status_code == 200

        r_openapi = await client.get("/openapi.json")
        assert r_openapi.status_code == 200
        data = r_openapi.json()
        assert data["info"]["title"] == "MDM Syniti Analysis Orchestrator API"


@pytest.mark.anyio
async def test_redis_down_at_startup_graceful_in_memory_fallback() -> None:
    """Verifies that when Redis connection fails during startup, lifespan gracefully falls back to InMemoryBackend, /ready reports cache_healthy=True, and requests succeed."""
    settings = ApiSettings(app_env="production", rate_limit_backend="redis")
    app = create_app(settings=settings, skip_index_check=True)

    with (
        patch("app.cache.backends.RedisBackend.ping", side_effect=OSError("Connection refused")),
        patch("app.knowledge.vector_index.VectorIndex.is_built", return_value=True),
        patch("app.knowledge.vector_index.VectorIndex.is_stale", return_value=(False, "")),
    ):
        async with app.router.lifespan_context(app):
            from app.cache.backends import InMemoryBackend

            assert isinstance(app.state.cache_backend, InMemoryBackend)

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                resp = await client.get("/ready")
                assert resp.status_code == 200
                data = resp.json()
                assert data["checks"]["cache_healthy"] is True
                assert data["status"] == "ready"
