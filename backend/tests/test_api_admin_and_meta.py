"""Tests for administrative controls, security gates, UI metadata, and Prometheus metrics."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from app.api.main import create_app
from app.api.models import ActivityItem, Project
from app.api.repositories import InMemoryActivityRepository, InMemoryProjectRepository
from app.api.settings import ApiSettings
from app.cache.backends import InMemoryBackend
from app.knowledge.models import Evidence, LearnedSapEntry, MatchType
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.store import KnowledgeStore
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_admin_authentication_gates() -> None:
    """Verifies that admin routes return 404 when key unset, 401 when missing, 403 when invalid."""
    # Scenario 1: ADMIN_API_KEY is unset -> 404
    settings_no_key = ApiSettings(app_env="development", admin_api_key=None)
    app_no_key = create_app(settings=settings_no_key, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app_no_key), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/v1/cache/stats", headers={"X-Admin-Key": "some-key"})
        assert resp.status_code == 404

    # Scenario 2: ADMIN_API_KEY is set
    correct_key = "super-secret-admin-passphrase-987"
    settings_with_key = ApiSettings(app_env="development", admin_api_key=SecretStr(correct_key))
    app_with_key = create_app(settings=settings_with_key, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app_with_key), base_url="http://testserver"
    ) as client:
        # Missing header -> 401
        r_missing = await client.get("/api/v1/cache/stats")
        assert r_missing.status_code == 401

        # Wrong header -> 403
        r_wrong = await client.get("/api/v1/cache/stats", headers={"X-Admin-Key": "wrong-secret"})
        assert r_wrong.status_code == 403

        # Correct header -> 200
        r_ok = await client.get("/api/v1/cache/stats", headers={"X-Admin-Key": correct_key})
        assert r_ok.status_code == 200
        data = r_ok.json()
        assert "l1_hits" in data
        assert "backend_healthy" in data


@pytest.mark.anyio
async def test_admin_cache_clear_and_learned_curation() -> None:
    """Verifies cache clearing, learned entry listing, approve/reject, and proposals."""
    admin_key = "test-admin-key-456"
    settings = ApiSettings(app_env="development", admin_api_key=SecretStr(admin_key))
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        headers = {"X-Admin-Key": admin_key}

        # Clear cache
        r_clear = await client.delete("/api/v1/cache?kind=all", headers=headers)
        assert r_clear.status_code == 200
        assert r_clear.json()["cleared"] is True

        # List learned
        r_learned = await client.get("/api/v1/admin/learned", headers=headers)
        assert r_learned.status_code == 200
        assert isinstance(r_learned.json(), list)

        # Glossary proposals
        r_proposals = await client.get("/api/v1/admin/glossary/proposals", headers=headers)
        assert r_proposals.status_code == 200
        assert isinstance(r_proposals.json(), list)


@pytest.mark.anyio
async def test_ui_metadata_and_examples() -> None:
    """Verifies workbook examples, user profile, projects, and activity endpoints."""
    settings = ApiSettings(app_env="development")

    # Inject in-memory repositories
    fake_projects = [
        Project(
            id="test-p1",
            name="Test Project",
            description="Unit test project",
            status="active",
            release_count=2,
            milestone_count=5,
        )
    ]
    fake_activity = [
        ActivityItem(
            id="act-test-1",
            status="complete",
            run_type="Validation",
            table_name="MARA",
            subtitle="General data",
            project_id="test-p1",
            finished_at="2026-11-05T12:00:00Z",
        )
    ]

    app = create_app(settings=settings, skip_index_check=True)
    app.state.project_repo = InMemoryProjectRepository(fake_projects)
    app.state.activity_repo = InMemoryActivityRepository(fake_activity)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # /examples
        r_ex = await client.get("/api/v1/examples")
        assert r_ex.status_code == 200
        examples = r_ex.json()
        assert len(examples) == 11
        for ex in examples:
            assert "id" in ex
            assert "title" in ex
            assert "sql" in ex
            # Must NEVER contain expected output fields
            assert "expected" not in ex
            assert "Opportunity Definition" not in ex

        # /me
        r_me = await client.get("/api/v1/me")
        assert r_me.status_code == 200
        me = r_me.json()
        assert "email" in me
        assert "roles" in me

        # /projects
        r_proj = await client.get("/api/v1/projects")
        assert r_proj.status_code == 200
        projs = r_proj.json()
        assert len(projs) == 1
        assert projs[0]["id"] == "test-p1"

        # /projects/{id}
        r_p1 = await client.get("/api/v1/projects/test-p1")
        assert r_p1.status_code == 200
        assert r_p1.json()["name"] == "Test Project"

        # /activity
        r_act = await client.get("/api/v1/activity?limit=10")
        assert r_act.status_code == 200
        acts = r_act.json()
        assert len(acts) == 1
        assert acts[0]["table_name"] == "MARA"


@pytest.mark.anyio
async def test_metrics_endpoint_and_labels() -> None:
    """Verifies that /metrics outputs Prometheus text format and contains low-cardinality labels."""
    settings = ApiSettings(app_env="development", metrics_enabled=True)
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        await client.get("/health")
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text

        assert "http_requests_total" in body
        assert "http_request_duration_seconds" in body
        # Labels must never contain raw SQL or client IPs
        assert "SELECT" not in body
        assert "127.0.0.1" not in body


@pytest.mark.anyio
async def test_admin_protected_metrics_in_production() -> None:
    """Verifies that in production with METRICS_REQUIRE_ADMIN, /metrics requires valid admin key and returns 401/403/404 properly."""
    admin_key = "prod-metrics-key-888"
    settings = ApiSettings(
        app_env="production",
        metrics_enabled=True,
        metrics_require_admin=True,
        admin_api_key=SecretStr(admin_key),
    )
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        # 1. Missing header -> 401
        r_unauth = await client.get("/metrics")
        assert r_unauth.status_code == 401

        # 2. Wrong header -> 403
        r_forbidden = await client.get("/metrics", headers={"X-Admin-Key": "bad-key"})
        assert r_forbidden.status_code == 403

        # 3. Valid key -> 200 with Prometheus metrics
        r_ok = await client.get("/metrics", headers={"X-Admin-Key": admin_key})
        assert r_ok.status_code == 200
        assert "http_requests_total" in r_ok.text

        # 4. Metrics disabled -> 404
        settings_disabled = ApiSettings(
            app_env="production",
            metrics_enabled=False,
            metrics_require_admin=True,
            admin_api_key=SecretStr(admin_key),
        )
        app_disabled = create_app(settings=settings_disabled, skip_index_check=True)
        async with AsyncClient(
            transport=ASGITransport(app=app_disabled), base_url="http://testserver"
        ) as client_dis:
            r_dis = await client_dis.get("/metrics", headers={"X-Admin-Key": admin_key})
            assert r_dis.status_code == 404


@pytest.mark.anyio
async def test_admin_learned_approve_and_reject_with_audit_trail(tmp_path: Path) -> None:
    """Verifies approving and rejecting learned SAP entries, verifying status updates and audit.jsonl logging."""
    admin_key = "test-learned-admin-777"
    settings = ApiSettings(app_env="development", admin_api_key=SecretStr(admin_key))
    app = create_app(settings=settings, skip_index_check=True)

    k_settings = KnowledgeSettings(custom_learned_dir=tmp_path)
    test_store = KnowledgeStore(settings=k_settings)

    # Seed a valid pending learned entry
    entry = LearnedSapEntry(
        id="MARA_ZCUSTOM1",
        table="MARA",
        field="ZCUSTOM1",
        description="Custom legacy identifier on material master",
        evidence=Evidence(
            chunk_id="chunk-test-mara-1",
            source_file="SAP_Dictionary.pdf",
            page=42,
            evidence_text="MARA GENERAL MATERIAL DATA ZCUSTOM1 LEGACY IDENTIFIER",
            retrieval_score=0.92,
            match_type=MatchType.exact,
        ),
        confidence=0.9,
        created_at="2026-11-05T10:00:00Z",
        last_seen_at="2026-11-05T10:00:00Z",
        status="pending",
    )
    test_store.sap_learned_store.save_entry(entry)

    with patch("app.api.routes_admin.KnowledgeStore", return_value=test_store):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            headers = {
                "X-Admin-Key": admin_key,
                "X-User-Email": "lead_auditor@enterprise.com",
            }

            # 1. Approve entry
            r_app = await client.post(
                "/api/v1/admin/learned/MARA_ZCUSTOM1/approve", headers=headers
            )
            assert r_app.status_code == 200
            data_app = r_app.json()
            assert data_app["status"] == "approved"
            assert data_app["auditor"] == "lead_auditor@enterprise.com"

            # Check store persisted status
            updated_entries = test_store.list_learned_sap(status="approved")
            assert len(updated_entries) == 1
            assert updated_entries[0].id == "MARA_ZCUSTOM1"

            # 2. Reject entry
            r_rej = await client.post("/api/v1/admin/learned/MARA_ZCUSTOM1/reject", headers=headers)
            assert r_rej.status_code == 200
            data_rej = r_rej.json()
            assert data_rej["status"] == "rejected"

            # 3. Non-existent entry -> 404
            r_404 = await client.post(
                "/api/v1/admin/learned/NONEXISTENT_FIELD/approve", headers=headers
            )
            assert r_404.status_code == 404

            # 4. Verify audit.jsonl log trail
            audit_file = tmp_path / "audit.jsonl"
            assert audit_file.exists()
            records = [
                json.loads(line)
                for line in audit_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            actions = [r["action"] for r in records]
            assert "approved_by_lead_auditor@enterprise.com" in actions
            assert "rejected_by_lead_auditor@enterprise.com" in actions


@pytest.mark.anyio
async def test_admin_cache_clear_all_and_individual_tiers() -> None:
    """Verifies clearing cache across all individual tiers (l1, l2, l3, all) and prefix isolation."""
    admin_key = "test-cache-admin-555"
    settings = ApiSettings(app_env="development", admin_api_key=SecretStr(admin_key))
    app = create_app(settings=settings, skip_index_check=True)

    fake_backend = InMemoryBackend()
    # Seed cache keys in different tiers
    await fake_backend.set("syniti:l1:test-k1", "val1")
    await fake_backend.set("syniti:l2:test-k2", "val2")
    await fake_backend.set("syniti:l3:test-k3", "val3")

    with patch("app.api.routes_admin.get_cache_backend", return_value=fake_backend):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            headers = {"X-Admin-Key": admin_key}

            # 1. Clear L1 tier only
            r_l1 = await client.delete("/api/v1/cache?kind=l1", headers=headers)
            assert r_l1.status_code == 200
            assert r_l1.json() == {"cleared": True, "kind": "l1"}
            assert await fake_backend.get("syniti:l1:test-k1") is None
            assert await fake_backend.get("syniti:l2:test-k2") == "val2"

            # 2. Clear L2 tier only
            r_l2 = await client.delete("/api/v1/cache?kind=l2", headers=headers)
            assert r_l2.status_code == 200
            assert r_l2.json() == {"cleared": True, "kind": "l2"}
            assert await fake_backend.get("syniti:l2:test-k2") is None
            assert await fake_backend.get("syniti:l3:test-k3") == "val3"

            # 3. Clear L3 tier only
            r_l3 = await client.delete("/api/v1/cache?kind=l3", headers=headers)
            assert r_l3.status_code == 200
            assert r_l3.json() == {"cleared": True, "kind": "l3"}
            assert await fake_backend.get("syniti:l3:test-k3") is None

            # 4. Clear all tiers
            await fake_backend.set("syniti:l1:k-all", "v-all")
            r_all = await client.delete("/api/v1/cache?kind=all", headers=headers)
            assert r_all.status_code == 200
            assert r_all.json() == {"cleared": True, "kind": "all"}
            assert await fake_backend.get("syniti:l1:k-all") is None

            # 5. Invalid kind -> 422
            r_invalid = await client.delete("/api/v1/cache?kind=l99", headers=headers)
            assert r_invalid.status_code == 422


@pytest.mark.anyio
async def test_post_projects_returns_501_not_implemented() -> None:
    """Verifies POST /api/v1/projects returns 501 NOT_IMPLEMENTED contract."""
    settings = ApiSettings(app_env="development")
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/projects",
            json={
                "name": "New Migration Project",
                "description": "Project test description",
            },
        )
        assert resp.status_code == 501
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "NOT_IMPLEMENTED"
        assert "Project creation is not available yet" in data["error"]["message"]


@pytest.mark.anyio
async def test_metadata_refresh_stream_returns_501_not_implemented() -> None:
    """Verifies POST /api/v1/metadata/refresh/stream returns 501 NOT_IMPLEMENTED contract."""
    settings = ApiSettings(app_env="development")
    app = create_app(settings=settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            "/api/v1/metadata/refresh/stream",
            json={"skp_url": "https://skp.enterprise.internal/api"},
        )
        assert resp.status_code == 501
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == "NOT_IMPLEMENTED"
        assert "Metadata refresh" in data["error"]["message"]


@pytest.mark.anyio
async def test_llm_settings_view_contract_and_zero_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies GET /api/v1/llm/settings exposes safe read-only diagnostics with no secret values."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-superSecretLiveKey1234567890abcdef")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom-gateway.internal/v1/chat")
    from app.agents.settings import get_llm_settings

    get_llm_settings.cache_clear()

    try:
        settings = ApiSettings(app_env="development")
        app = create_app(settings=settings, skip_index_check=True)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            resp = await client.get("/api/v1/llm/settings")
            assert resp.status_code == 200
            data = resp.json()

            # Required fields
            assert "OpenAI" in data["provider"]
            assert "model" in data
            assert "structured_output_mode" in data
            assert "api_mode" in data
            assert data["api_key_configured"] is True
            # Base URL is host-only or path-free
            assert data["base_url"] == "https://custom-gateway.internal"
            # Ensure NO secret key leakage in payload text
            raw_text = resp.text
            assert "sk-proj-superSecretLiveKey1234567890abcdef" not in raw_text
            assert "superSecret" not in raw_text
    finally:
        get_llm_settings.cache_clear()


@pytest.mark.anyio
async def test_me_principal_configurable_from_settings() -> None:
    """Verifies GET /api/v1/me returns configured placeholder principal and dynamically computes initials."""
    custom_settings = ApiSettings(
        app_env="development",
        placeholder_principal_name="Jai Shankar",
        placeholder_principal_email="jai.shankar@example.com",
    )
    app = create_app(settings=custom_settings, skip_index_check=True)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/v1/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Jai Shankar"
        assert data["email"] == "jai.shankar@example.com"
        assert data["initials"] == "JS"
        assert "roles" in data
