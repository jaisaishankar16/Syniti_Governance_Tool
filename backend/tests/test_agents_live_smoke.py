"""Live smoke tests running all 11 examples through gpt-4o-mini (skipped unless RUN_LIVE_LLM=1)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from app.agents.models import AgentDeps, RunTrace
from app.agents.parallel import analyze_with_agents
from app.agents.settings import LLMSettings
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.store import KnowledgeStore
from app.parser.sql_parser import SqlParser


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM") != "1",
    reason="Live smoke tests skipped unless RUN_LIVE_LLM=1",
)
def test_live_smoke_11_examples():
    """Runs all 11 Excel examples through both agents using gpt-4o-mini."""
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "examples.json"
    if not fixture_path.exists():
        fixture_path = Path("backend/tests/fixtures/examples.json").resolve()

    with open(fixture_path, "r", encoding="utf-8") as f:
        examples = json.load(f)

    async def _test():
        settings = LLMSettings(
            llm_model="gpt-4o-mini",
            llm_temperature=0.1,
            llm_timeout_s=60.0,
        )
        store = KnowledgeStore()
        sap_retriever = SapRetriever()
        sql_retriever = SqlDocRetriever()
        parser = SqlParser()

        total_in_tokens = 0
        total_out_tokens = 0

        print("\n" + "=" * 80)
        print("                 LIVE SMOKE TEST: 11 EXAMPLES (gpt-4o-mini)")
        print("=" * 80)

        for idx, ex in enumerate(examples):
            sql = ex["input_sql"]
            sheet = ex.get("source_sheet", f"sheet_{idx}")
            parsed = parser.parse(sql)

            def deps_factory(curr_idx: int = idx) -> tuple[AgentDeps, AgentDeps]:
                sql_deps = AgentDeps(
                    store=store,
                    sap_retriever=sap_retriever,
                    sql_retriever=sql_retriever,
                    settings=settings,
                    request_id=f"smoke_{curr_idx}",
                    run_trace=RunTrace(),
                )
                sap_deps = AgentDeps(
                    store=store,
                    sap_retriever=sap_retriever,
                    sql_retriever=sql_retriever,
                    settings=settings,
                    request_id=f"smoke_{curr_idx}",
                    run_trace=RunTrace(),
                )
                return sql_deps, sap_deps

            res = await analyze_with_agents(
                parsed=parsed,
                deps_factory=deps_factory,
                settings=settings,
            )

            assert res.degraded is False, f"Degraded execution on {sheet}"
            assert res.sql.status == "ok"
            assert res.sap.status == "ok"
            assert res.sql.output is not None
            assert res.sap.output is not None

            # Verify SAP tables resolved
            if parsed.view_naming and parsed.view_naming.sap_table:
                expected_tbl = parsed.view_naming.sap_table
                resolved_tables = [t.resolved_sap_table or t.name for t in res.sap.output.tables]
                assert any(expected_tbl in t_name for t_name in resolved_tables), (
                    f"Expected SAP table {expected_tbl} not resolved in {sheet}"
                )

            # Accumulate usage
            q_tokens = (
                res.sql.usage.input_tokens
                + res.sql.usage.output_tokens
                + res.sap.usage.input_tokens
                + res.sap.usage.output_tokens
            )
            total_in_tokens += res.sql.usage.input_tokens + res.sap.usage.input_tokens
            total_out_tokens += res.sql.usage.output_tokens + res.sap.usage.output_tokens

            print(
                f"Example {idx + 1:02d} ({sheet[:20]}): OK | {res.total_latency_ms:.0f} ms | {q_tokens} tokens"
            )

        # gpt-4o-mini pricing: $0.15 / 1M input tokens, $0.60 / 1M output tokens
        cost_in = (total_in_tokens / 1_000_000) * 0.15
        cost_out = (total_out_tokens / 1_000_000) * 0.60
        total_cost = cost_in + cost_out

        print("-" * 80)
        print(f"Total Input Tokens:   {total_in_tokens:,}")
        print(f"Total Output Tokens:  {total_out_tokens:,}")
        print(f"Total Tokens:         {total_in_tokens + total_out_tokens:,}")
        print(f"Estimated Total Cost: ${total_cost:.5f}")
        print("=" * 80 + "\n")

    asyncio.run(_test())
