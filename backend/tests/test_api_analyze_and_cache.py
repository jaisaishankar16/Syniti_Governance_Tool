"""Tests for /api/v1/analyze endpoint, caching behavior, and input validation."""

from __future__ import annotations

import pytest
from app.api.main import create_app
from app.api.settings import ApiSettings
from app.cache.backends import InMemoryBackend
from app.orchestrator.orchestrator import analyze as real_analyze
from httpx import ASGITransport, AsyncClient

from tests.support.fakes import FakeModel


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mock_valid_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configures a valid dummy API key and clears lru_cache."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-unitTestValidKey1234567890abcdef12345")
    from app.agents.settings import get_llm_settings

    get_llm_settings.cache_clear()
    yield
    get_llm_settings.cache_clear()


@pytest.mark.anyio
async def test_analyze_happy_path_and_cache_hit(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies happy path analysis and that identical subsequent requests hit L1 cache."""
    settings = ApiSettings(app_env="development")
    backend = InMemoryBackend()
    fake_model = FakeModel()

    async def _test_orchestrator(*args, **kwargs):
        kwargs["model"] = fake_model
        kwargs["cache_backend"] = backend
        return await real_analyze(*args, **kwargs)

    app = create_app(
        settings=settings,
        orchestrator_factory=_test_orchestrator,
        skip_index_check=True,
    )

    sql = "SELECT MARC.MATNR, MARA.MTART FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN dbo.MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # Run 1: Cache miss
        resp1 = await client.post(
            "/api/v1/analyze",
            json={"sql": sql, "force_refresh": False},
        )
        assert resp1.status_code == 200
        bi1 = resp1.json()
        assert bi1["meta"]["cache_status"] == "miss"
        assert bi1["meta"]["ai_generated"] is True
        assert bi1["governance"]["rule"] != ""
        req_id_1 = resp1.headers.get("X-Request-ID")
        assert req_id_1 is not None
        assert bi1["meta"]["request_id"] == req_id_1

        # Run 2: Identical query must hit L1 cache
        resp2 = await client.post(
            "/api/v1/analyze",
            json={"sql": sql, "force_refresh": False},
        )
        assert resp2.status_code == 200
        bi2 = resp2.json()
        assert bi2["meta"]["cache_status"] == "hit_final"
        assert bi2["governance"]["rule"] == bi1["governance"]["rule"]

        # Run 3: force_refresh=True forces cache bypass
        resp3 = await client.post(
            "/api/v1/analyze",
            json={"sql": sql, "force_refresh": True},
        )
        assert resp3.status_code == 200
        bi3 = resp3.json()
        assert bi3["meta"]["cache_status"] in ("miss", "refresh")


@pytest.mark.anyio
async def test_analyze_validation_errors(
    mock_valid_llm_settings: None,
) -> None:
    """Verifies rejection of invalid JSON, empty body, missing SQL, and non-SQL prose."""
    settings = ApiSettings(app_env="development")
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # 1. Missing SQL field
        resp_missing = await client.post("/api/v1/analyze", json={})
        assert resp_missing.status_code == 422
        data_missing = resp_missing.json()
        assert data_missing["error"]["code"] == "VALIDATION_ERROR"

        # 2. Empty SQL string
        resp_empty = await client.post("/api/v1/analyze", json={"sql": ""})
        assert resp_empty.status_code == 422

        # 3. Non-SQL prose rejected by pre-flight guardrail
        resp_non_sql = await client.post(
            "/api/v1/analyze",
            json={"sql": "Hello system, please explain data migration without SQL"},
        )
        assert resp_non_sql.status_code == 422
        data_non_sql = resp_non_sql.json()
        assert data_non_sql["error"]["code"] == "INPUT_REJECTED"

        # 4. Unknown extra field rejected (extra='forbid')
        resp_extra = await client.post(
            "/api/v1/analyze",
            json={"sql": "SELECT 1", "unknown_field": True},
        )
        assert resp_extra.status_code == 422
        assert resp_extra.json()["error"]["code"] == "VALIDATION_ERROR"
