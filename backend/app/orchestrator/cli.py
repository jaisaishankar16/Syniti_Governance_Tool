"""Command-Line Interface (CLI) for the MDM Syniti Orchestrator.

Executes end-to-end analysis across guardrails, cache, parallel agents,
and synthesis. Under Project Rules (see docs/PROJECT_RULES.md), execution with an
OpenAI-compatible LLM is the only operational mode.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from app.agents.settings import ENV_FILE_PATH, get_key_status, get_llm_settings, is_valid_api_key
from app.cache.backends import get_cache_backend
from app.cache.stats import get_stats_tracker
from app.errors import AppError
from app.guardrails.models import GuardrailBlocked
from app.orchestrator.orchestrator import analyze
from app.orchestrator.settings import get_orchestrator_settings
from app.synthesizer.models import BusinessIntelligence


def _load_example_sql(example_query: str) -> tuple[str, str]:
    """Loads a SQL string from backend/tests/fixtures/examples.json by index or sheet name."""
    fixture_path = (
        Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "examples.json"
    )
    if not fixture_path.exists():
        alt_path = Path("backend/tests/fixtures/examples.json")
        if alt_path.exists():
            fixture_path = alt_path.resolve()

    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture file not found: {fixture_path}")

    with open(fixture_path, "r", encoding="utf-8") as f:
        examples = json.load(f)

    if example_query.isdigit():
        idx = int(example_query)
        if 0 <= idx < len(examples):
            ex = examples[idx]
            return ex.get("id", ex.get("source_sheet", f"sheet_{idx}")), ex["input_sql"]

    query_lower = example_query.lower()
    for ex in examples:
        sheet = str(ex.get("id", ex.get("source_sheet", ""))).lower()
        rule = str(ex.get("rule_name", ex.get("expected", {}).get("Rule", ""))).lower()
        if query_lower in sheet or query_lower in rule or sheet in query_lower:
            return ex.get("id", ex.get("source_sheet", "matched")), ex["input_sql"]

    return examples[0].get("id", "Sheet1"), examples[0]["input_sql"]


def _print_summary_card(
    bi: BusinessIntelligence, title: str = "MDM SYNITI ANALYSIS RESULT"
) -> None:
    """Prints a structured summary card of the BusinessIntelligence result."""
    print("=" * 80)
    print(f"                       {title}")
    print("=" * 80)
    print(f"Rule:              {bi.governance.rule}")
    print(f"Subject Area:      {bi.governance.subject_area}")
    print(f"Business Process:  {bi.governance.business_process}")
    print(f"Dataset:           {bi.governance.dataset}")
    print(f"Concept:           {bi.governance.business_concept.term}")
    print(f"SAP Module:        {bi.sap_module}")
    print(f"Query Type:        {bi.query_type}")
    print(f"Risk Level:        {bi.risk_summary.level.upper()}")
    if bi.risk_summary.items:
        print(f"Risk Items:        {', '.join(bi.risk_summary.items[:3])}")
    print("-" * 80)
    print("EXECUTION METADATA:")
    print(f"  Request ID:      {bi.meta.request_id}")
    print(f"  Cache Status:    {bi.meta.cache_status.upper()}")
    print(
        f"  Cache Detail:    SQL={bi.meta.cache_detail.sql_agent}, "
        f"SAP={bi.meta.cache_detail.sap_agent}"
        + (f", Similarity={bi.meta.similarity_score:.4f}" if bi.meta.similarity_score else "")
    )
    print(f"  AI Generated:    {bi.meta.ai_generated}")
    print(f"  Degraded:        {bi.meta.degraded}")
    if bi.meta.degraded_reasons:
        print(f"  Degraded Reasons:{', '.join(bi.meta.degraded_reasons)}")
    if bi.meta.warnings:
        print(f"  Warnings:        {'; '.join(bi.meta.warnings)}")
    print("-" * 80)
    print("PERFORMANCE & COST:")
    print(f"  Total Time:      {bi.meta.stage_timings_ms.get('total', 0.0):.2f} ms")
    if bi.meta.stage_timings_ms:
        stage_strs = [f"{k}={v:.1f}ms" for k, v in bi.meta.stage_timings_ms.items() if k != "total"]
        print(f"  Stage Timings:   {', '.join(stage_strs)}")
    print(
        f"  Tokens:          Total={bi.meta.token_usage.total} "
        f"(SQL={bi.meta.token_usage.sql}, SAP={bi.meta.token_usage.sap}, Synth={bi.meta.token_usage.synthesizer})"
    )
    print("=" * 80)


def _count_tokens(text: str) -> tuple[int, str]:
    """Calculates token count using tiktoken (cl100k_base or o200k_base) if cached, else character / 4 fallback."""
    try:
        import tiktoken

        cache_dir = os.environ.get("TIKTOKEN_CACHE_DIR")
        if cache_dir and Path(cache_dir).exists():
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text, disallowed_special=())), "tiktoken (cl100k_base)"
    except Exception:  # noqa: BLE001, S110
        pass
    return (len(text) + 3) // 4, "fallback (chars/4)"


def _handle_inspect_inputs(sql_text: str, source_name: str, settings: Any) -> None:
    """Runs pipeline up to LLM calls and prints parser output, rendered prompts, token counts, context window %, and unverified warnings."""
    from app.agents.knowledge_pack import build_sap_pack, build_sql_pack
    from app.agents.models import SAPAnalysis, SQLAnalysis
    from app.agents.sap_agent import build_sap_agent_input, create_sap_agent
    from app.agents.sql_agent import build_sql_agent_input, create_sql_agent
    from app.guardrails.pipeline import pre_flight_pipeline
    from app.knowledge.glossary import GlossaryHelper
    from app.knowledge.store import KnowledgeStore
    from app.parser.sql_parser import SqlParser
    from app.synthesizer.deterministic import baseline_opportunity_query
    from app.synthesizer.synthesizer import (
        _load_prompt_template as _load_synth_prompt_template,
    )
    from app.synthesizer.synthesizer import (
        build_synthesizer_input,
    )

    context_window = 128_000  # 128k context window for gpt-4o-mini

    # 1. Pre-flight & SQL Parser
    sanitized = pre_flight_pipeline(sql_text)
    parser = SqlParser()
    parsed = parser.parse(sanitized.sanitized_sql)

    # 2. Knowledge Packs
    store = KnowledgeStore()
    sql_pack = build_sql_pack(parsed, store, max_items=settings.pack_max_items)
    sap_pack = build_sap_pack(parsed, store, max_items=settings.pack_max_items)

    # 3. Agent Prompts & Inputs
    sql_agent, sql_p_ver = create_sql_agent("dummy", settings)
    sap_agent, sap_p_ver = create_sap_agent("dummy", settings)
    synth_instructions, synth_p_ver = _load_synth_prompt_template()

    sql_user_input = build_sql_agent_input(parsed, sql_pack)
    sap_user_input = build_sap_agent_input(parsed, sap_pack)

    baseline_opp = baseline_opportunity_query(parsed)
    synth_user_input = build_synthesizer_input(
        parsed=parsed,
        sql_analysis=SQLAnalysis(
            query_purpose="Query analysis",
            statement_type="SELECT",
            technical_summary="Technical query analysis summary",
        ),
        sap_analysis=SAPAnalysis(sap_module="MM", client_handling="Client-independent"),
        baseline_opp=baseline_opp,
        boundary_id="inspect-inputs-boundary",
        canary_token=sanitized.canary_token,
        glossary_helper=GlossaryHelper(),
    )

    # 4. Check for Unverified Knowledge
    unverified_items: list[str] = []
    for sql_item in sql_pack.resolved:
        if not sql_item.verified:
            unverified_items.append(f"SQL Keyword: {sql_item.keyword}")
    for sap_item in sap_pack.resolved:
        if not sap_item.verified:
            item_desc = f"{sap_item.table}.{sap_item.field}" if sap_item.field else sap_item.table
            unverified_items.append(f"SAP {sap_item.level.title()}: {item_desc}")

    # 5. Token estimates
    sql_instructions_str = (
        sql_agent.instructions
        if isinstance(sql_agent.instructions, str)
        else str(sql_agent.instructions or "")
    )
    sap_instructions_str = (
        sap_agent.instructions
        if isinstance(sap_agent.instructions, str)
        else str(sap_agent.instructions or "")
    )
    sql_sys_tok, method = _count_tokens(sql_instructions_str)
    sql_usr_tok, _ = _count_tokens(sql_user_input)
    sap_sys_tok, _ = _count_tokens(sap_instructions_str)
    sap_usr_tok, _ = _count_tokens(sap_user_input)
    synth_sys_tok, _ = _count_tokens(synth_instructions)
    synth_usr_tok, _ = _count_tokens(synth_user_input)

    print("=" * 80)
    print(f"             INSPECT INPUTS & PROMPT PREP: [{source_name}]")
    print("=" * 80)
    print("1. SQL PARSER OUTPUT:")
    print(f"  Dialect:         {getattr(parsed, 'dialect', 'tsql (MSSQL)')}")
    print(f"  Parse Quality:   {parsed.parse_quality}")
    print(f"  Normalized SQL:  {parsed.normalized_sql[:120]}...")
    print(f"  Tables ({len(parsed.tables)}):     {', '.join(t.raw_name for t in parsed.tables)}")
    print(
        f"  Columns ({len(parsed.columns)}):    {', '.join(c.column for c in parsed.columns[:6])}"
        + ("..." if len(parsed.columns) > 6 else "")
    )
    naming_summary = [f"{t.raw_name}->{t.naming.object_kind}" for t in parsed.tables if t.naming]
    print(f"  Naming Resolve:  {', '.join(naming_summary) if naming_summary else 'None'}")
    violations = [v.message for t in parsed.tables if t.naming for v in t.naming.violations]
    if violations:
        print(f"  Naming Violations: {', '.join(violations)}")
    print("-" * 80)

    print("2. PROMPT TEMPLATES & VERSIONS:")
    print(f"  SQL Agent Version:         {sql_p_ver}")
    print(f"  SAP Agent Version:         {sap_p_ver}")
    print(f"  Synthesizer Version:       {synth_p_ver}")
    print("-" * 80)

    print("3. KNOWLEDGE PACKS:")
    print(f"  SQL Pack Resolved:         {len(sql_pack.resolved)} items")
    print(f"  SQL Pack Unresolved:       {len(sql_pack.unresolved)} items")
    print(f"  SAP Pack Resolved:         {len(sap_pack.resolved)} items")
    print(f"  SAP Pack Unresolved:       {len(sap_pack.unresolved)} items")
    print(f"  SAP Naming Facts:          {len(sap_pack.naming_facts)} facts")

    if unverified_items:
        print("\n  [!] WARNING: UNVERIFIED KNOWLEDGE INCLUDED (Lower Confidence):")
        for u_item in unverified_items[:8]:
            print(f"      * {u_item} [UNVERIFIED - Lower Confidence]")
        if len(unverified_items) > 8:
            print(f"      ... and {len(unverified_items) - 8} more")
    else:
        print("  Confidence Status:         All knowledge items verified")
    print("-" * 80)

    print(f"4. TOKEN COUNT ESTIMATES (Method: {method}, Context Window: {context_window:,}):")
    print(f"  {'Section':<42} | {'Tokens':>8} | {'% Context':>10}")
    print("  " + "-" * 66)
    sections = [
        ("SQL Agent System Instructions", sql_sys_tok),
        ("SQL Agent User Input (with pack)", sql_usr_tok),
        ("SAP Agent System Instructions", sap_sys_tok),
        ("SAP Agent User Input (with pack)", sap_usr_tok),
        ("Synthesizer System Instructions", synth_sys_tok),
        ("Synthesizer User Input (few-shot + packs)", synth_usr_tok),
        ("Max Single-Turn Context (Synthesizer)", synth_sys_tok + synth_usr_tok),
    ]
    for name, tok in sections:
        pct = (tok / context_window) * 100.0
        print(f"  {name:<42} | {tok:>8,} | {pct:>9.2f}%")
    print("=" * 80)


def _print_stats() -> None:
    """Prints live cache hit/miss statistics."""
    tracker = get_stats_tracker()
    print("=" * 80)
    print("                         CACHE & PIPELINE METRICS")
    print("=" * 80)
    print(f"L1 Final Cache:    Hits={tracker.l1_hits}, Misses={tracker.l1_misses}")
    print(f"L2 SQL Cache:      Hits={tracker.l2_sql_hits}, Misses={tracker.l2_sql_misses}")
    print(f"L2 SAP Cache:      Hits={tracker.l2_sap_hits}, Misses={tracker.l2_sap_misses}")
    print(
        f"L3 Similarity:     Reuses={tracker.l3_similarity_reuses}, Hints={tracker.l3_similarity_hints}"
    )
    print(f"Tokens Saved:      {tracker.estimated_tokens_saved}")
    print("=" * 80)


async def async_main(args: argparse.Namespace) -> None:
    orch_settings = get_orchestrator_settings()
    llm_settings = get_llm_settings()

    # Determine if this invocation is a standalone utility operation (stats / clear-cache only)
    has_analysis_request = bool(
        args.example or args.sql or args.file or args.twice or args.force_refresh
    )
    is_standalone_utility = (args.stats or args.clear_cache) and not has_analysis_request

    if is_standalone_utility:
        if args.clear_cache:
            backend = await get_cache_backend(orch_settings.cache_backend, orch_settings.redis_url)
            await backend.clear()
            print("SUCCESS: Cache cleared completely.")
        if args.stats:
            if args.clear_cache:
                print()
            _print_stats()
        return

    # If cache clear was requested before an analysis run
    if args.clear_cache:
        backend = await get_cache_backend(orch_settings.cache_backend, orch_settings.redis_url)
        await backend.clear()
        print("SUCCESS: Cache cleared completely.")

    # Resolve SQL input
    source_name = "custom"
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"ERROR: File not found: {file_path}", file=sys.stderr)
            sys.exit(1)
        sql_input = file_path.read_text(encoding="utf-8")
        source_name = file_path.name
    elif args.sql:
        sql_input = args.sql
        source_name = "inline_sql"
    else:
        example_id = args.example or "sheet1"
        source_name, sql_input = _load_example_sql(example_id)

    # Diagnostic mode: inspect inputs and prompt prep without LLM call
    if args.inspect_inputs:
        _handle_inspect_inputs(sql_input, source_name, llm_settings)
        return

    # Fail fast if no valid API key is configured
    raw_key = llm_settings.openai_api_key.get_secret_value() if llm_settings.openai_api_key else ""
    if not is_valid_api_key(raw_key):
        status = get_key_status(llm_settings)
        env_path = status["env_path"]
        if not status["env_exists"]:
            env_reason = "missing"
        elif status["env_is_empty"]:
            env_reason = "empty"
        else:
            env_reason = "contains invalid or placeholder key"

        os_val = os.environ.get("OPENAI_API_KEY")
        os_reason = (
            "not set"
            if os_val is None
            else ("empty string" if not os_val.strip() else "obvious placeholder")
        )

        print(
            f"ERROR: Cannot run analysis with model '{llm_settings.llm_model}': No valid API key.\n\n"
            f"Where looked:\n"
            f"  1. OS Environment Variable: OPENAI_API_KEY (status: {os_reason})\n"
            f"  2. Dotenv file:             {env_path} (status: {env_reason})\n\n"
            "PowerShell steps to create .env file:\n"
            f"  Set-Content -Path \"{ENV_FILE_PATH}\" -Value 'OPENAI_API_KEY=sk-proj-yourActualKeyHere'\n\n"
            "Or set in your current PowerShell session:\n"
            '  $env:OPENAI_API_KEY = "sk-proj-yourActualKeyHere"\n',
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\nTarget SQL Source: [{source_name}]")

    # Execute Run 1
    print(f"Executing Run 1 (force_refresh={args.force_refresh})...")
    try:
        res1 = await analyze(
            sql=sql_input,
            force_refresh=args.force_refresh,
            settings=orch_settings,
            llm_settings=llm_settings,
            model=None,
        )
    except AppError as exc:
        print(f"\nERROR: [{exc.code.value}] {exc.message}", file=sys.stderr)
        if exc.details:
            print(f"Details: {exc.details}", file=sys.stderr)
        sys.exit(1)
    except GuardrailBlocked as exc:
        print(f"\nERROR: [GUARDRAIL_BLOCKED] {exc.message}", file=sys.stderr)
        if exc.report and exc.report.events:
            print(f"Violations: {'; '.join(e.message for e in exc.report.events)}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(res1.model_dump_json(indent=2))
    else:
        _print_summary_card(res1, title=f"RUN 1 RESULT - {source_name}")

    # If --twice requested, execute Run 2 to test cache persistence
    if args.twice:
        print("\nExecuting Run 2 (testing cache retrieval)...")
        try:
            res2 = await analyze(
                sql=sql_input,
                force_refresh=False,
                settings=orch_settings,
                llm_settings=llm_settings,
                model=None,
            )
        except AppError as exc:
            print(f"\nERROR in Run 2: [{exc.code.value}] {exc.message}", file=sys.stderr)
            sys.exit(1)
        except GuardrailBlocked as exc:
            print(f"\nERROR in Run 2: [GUARDRAIL_BLOCKED] {exc.message}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(res2.model_dump_json(indent=2))
        else:
            _print_summary_card(res2, title=f"RUN 2 RESULT (CACHE CHECK) - {source_name}")

        print("\nTWICE VERIFICATION:")
        print(f"  Run 1 Cache Status: {res1.meta.cache_status.upper()}")
        print(f"  Run 2 Cache Status: {res2.meta.cache_status.upper()}")
        is_hit = res2.meta.cache_status == "hit_final"
        print(f"  Cache Hit Verified: {'YES (Zero agent calls)' if is_hit else 'NO'}")
        print(f"  Run 2 Total Time:   {res2.meta.stage_timings_ms.get('total', 0.0):.2f} ms")

    # Print statistics if requested
    if args.stats:
        print()
        _print_stats()


def main() -> None:
    parser = argparse.ArgumentParser(description="MDM Syniti Analysis Orchestrator CLI")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--example",
        "-e",
        type=str,
        default=None,
        help="Example identifier or sheet name from examples.json (e.g. 'sheet1', 'sheet2', '0')",
    )
    input_group.add_argument(
        "--sql",
        "-s",
        type=str,
        help="Raw SQL string to analyze directly",
    )
    input_group.add_argument(
        "--file",
        "-f",
        type=Path,
        help="Path to SQL file to analyze",
    )

    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Bypass cache read and force complete re-analysis",
    )
    parser.add_argument(
        "--twice",
        action="store_true",
        help="Run analysis twice consecutively to test cache hit on second run",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print cache hit/miss statistics",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear cache storage before execution",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted summary card",
    )
    parser.add_argument(
        "--inspect-inputs",
        action="store_true",
        help="Inspect parser outputs, rendered prompts, token estimates, and context window %% without calling LLM",
    )

    args = parser.parse_args()

    # Windows event loop policy
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
