"""Unit tests for capability probes, auto modes resolution, prefetch mode, and dynamic fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.agents.capabilities import (
    compute_capability_key,
    get_process_fallback,
    record_process_fallback,
    resolve_effective_modes,
)
from app.agents.models import AgentDeps, RunTrace
from app.agents.parallel import analyze_with_agents
from app.agents.sap_agent import create_sap_agent
from app.agents.settings import LLMSettings
from app.agents.sql_agent import create_sql_agent
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.store import KnowledgeStore
from app.parser.sql_parser import SqlParser

from tests.support.fakes import FakeModel


def test_resolve_effective_modes_auto_defaults():
    """Default auto modes resolve to native structured output, native tools, and temp supported."""
    settings = LLMSettings()
    struct_mode, tools_mode, temp_supp = resolve_effective_modes(settings)

    assert struct_mode == "native"
    assert tools_mode == "native"
    assert temp_supp is True


def test_capabilities_file_drives_auto_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When model_capabilities.json records degraded capabilities, auto modes honor them."""
    caps_file = tmp_path / "model_capabilities.json"
    settings = LLMSettings(llm_model="custom-gateway-model", openai_base_url="https://gateway.corp")

    key = compute_capability_key(settings.llm_model, settings.openai_base_url)
    caps_data = {
        key: {
            "model": settings.llm_model,
            "base_url": settings.openai_base_url,
            "structured_outputs": False,
            "tool_calling": False,
            "temperature_supported": False,
            "probed_at": "2026-11-06T10:00:00Z",
        }
    }
    caps_file.write_text(json.dumps(caps_data))
    monkeypatch.setattr("app.agents.capabilities.CAPABILITIES_FILE", caps_file)

    struct_mode, tools_mode, temp_supp = resolve_effective_modes(settings)

    assert struct_mode == "json_prompt"
    assert tools_mode == "prefetch"
    assert temp_supp is False


def test_dynamic_fallback_recorded_on_schema_error():
    """record_process_fallback triggers immediate fallback for that process run."""
    model = "gateway-strict-model"
    base_url = "https://gateway.internal"

    assert get_process_fallback(model, base_url, "structured") is None
    record_process_fallback(model, base_url, "structured", True)
    assert get_process_fallback(model, base_url, "structured") is True

    settings = LLMSettings(llm_model=model, openai_base_url=base_url)
    struct_mode, _, _ = resolve_effective_modes(settings)
    assert struct_mode == "json_prompt"


def test_prefetch_mode_builds_agents_with_zero_tools():
    """In prefetch tools mode, agents receive tools=[] and write-back is disabled."""
    settings = LLMSettings(tools_mode="prefetch")
    fake_model = FakeModel()

    sql_agent, _ = create_sql_agent(model=fake_model, settings=settings)
    sap_agent, _ = create_sap_agent(model=fake_model, settings=settings)

    assert len(sql_agent.tools) == 0
    assert len(sap_agent.tools) == 0


def test_prefetch_mode_execution():
    """analyze_with_agents executes in prefetch mode without invoking agent tools."""
    parser = SqlParser()
    store = KnowledgeStore()
    parsed = parser.parse("SELECT [MATNR] FROM [dbo].[MM_MARC_ECC_EU_R1_MARC_S]")

    def deps_factory():
        trace = RunTrace()
        return (
            AgentDeps(
                store=store,
                sap_retriever=SapRetriever(),
                sql_retriever=SqlDocRetriever(),
                run_trace=trace,
                request_id="test-req-prefetch",
                settings=settings,
            ),
            AgentDeps(
                store=store,
                sap_retriever=SapRetriever(),
                sql_retriever=SqlDocRetriever(),
                run_trace=trace,
                request_id="test-req-prefetch",
                settings=settings,
            ),
        )

    settings = LLMSettings(tools_mode="prefetch")
    fake_model = FakeModel()

    async def _run():
        return await analyze_with_agents(
            parsed=parsed,
            deps_factory=deps_factory,
            settings=settings,
            model=fake_model,
            request_id="test-req-prefetch",
        )

    import asyncio

    res = asyncio.run(_run())
    assert res.sql.status == "ok"
    assert res.sap.status == "ok"
