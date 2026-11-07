"""CLI interface for running SQLSemanticsAgent and SAPDomainAgent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from app.agents.knowledge_pack import build_sap_pack, build_sql_pack
from app.agents.models import AgentDeps, AgentsResult
from app.agents.parallel import analyze_with_agents
from app.agents.settings import (
    ENV_FILE_PATH,
    LLMSettings,
    get_key_status,
    is_valid_api_key,
)
from app.guardrails.models import GuardrailBlocked
from app.guardrails.pipeline import pre_flight_pipeline
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.store import KnowledgeStore
from app.parser.sql_parser import SqlParser


def _load_example_sql(example_query: str) -> tuple[str, str]:
    """Loads a SQL string from backend/tests/fixtures/examples.json by index or sheet name."""
    fixture_path = (
        Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "examples.json"
    )
    if not fixture_path.exists():
        # Fallback search
        alt_path = Path("backend/tests/fixtures/examples.json")
        if alt_path.exists():
            fixture_path = alt_path.resolve()

    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture file not found: {fixture_path}")

    with open(fixture_path, "r", encoding="utf-8") as f:
        examples = json.load(f)

    # Try numeric index
    if example_query.isdigit():
        idx = int(example_query)
        if 0 <= idx < len(examples):
            ex = examples[idx]
            return ex.get("source_sheet", f"sheet_{idx}"), ex["input_sql"]

    # Try string match on source_sheet or rule_name
    query_lower = example_query.lower()
    for ex in examples:
        sheet = str(ex.get("source_sheet", "")).lower()
        rule = str(ex.get("rule_name", "")).lower()
        if query_lower in sheet or query_lower in rule or sheet in query_lower:
            return ex.get("source_sheet", "matched"), ex["input_sql"]

    # Fallback to first example if not found
    return examples[0].get("source_sheet", "sheet1"), examples[0]["input_sql"]


def _handle_check_env(settings: LLMSettings, do_ping: bool = False) -> None:
    """Inspects environment, .env file path and status, and optionally pings AI gateway."""
    status = get_key_status(settings)
    env_path = status["env_path"]
    if status["env_exists"]:
        env_state = "Empty" if status["env_is_empty"] else "Populated"
        env_desc = f"{env_path} (Status: Found, {env_state})"
    else:
        env_desc = f"{env_path} (Status: Not found)"

    key_var = status["key_variable"] or "None"
    key_src = status["key_source"] or "Not configured"
    key_len_desc = (
        f"{status['key_length']} characters"
        if status["has_valid_key"]
        else "0 (not configured)"
    )
    if status["has_valid_key"]:
        key_desc = f"configured (length: {status['key_length']})"
    else:
        key_desc = "Not configured"

    url_var = status["base_url_variable"] or "None (default)"
    url_src = status["base_url_source"] or "default"

    print("=" * 80)
    print("                        ENVIRONMENT & CONFIG CHECK")
    print("=" * 80)
    print(f".env Path:          {env_desc}")
    print(f"Key Variable:       {key_var}")
    print(f"Key Origin:         {key_src}")
    print(f"Key Length:         {key_len_desc}")
    print(f"API Key:            {key_desc}")
    print(f"Base URL Variable:  {url_var}")
    print(f"Base URL Origin:    {url_src}")
    if settings.openai_base_url:
        print(f"Base URL:           {settings.openai_base_url}")
    else:
        print("Base URL:           None (default: https://api.openai.com/v1)")
    print(f"Model:              {settings.llm_model}")
    print(f"API Mode:           {settings.llm_api_mode}")
    print(f"Structured:         {settings.structured_output_mode}")
    print(f"Tools Mode:         {settings.tools_mode}")
    print(f"Temperature:        {settings.llm_temperature}")
    print(f"Max Turns:          {settings.llm_max_turns}")
    print(f"Timeout:            {settings.llm_timeout_s}s")
    print("-" * 80)

    from app.embeddings.settings import get_embedding_settings

    emb_settings = get_embedding_settings()
    print(f"Embedding Model:    {emb_settings.embedding_model}")
    print(f"Dimensions:         {emb_settings.embedding_dimensions or 'Model Default'}")
    print(f"Embedding Base Host:{emb_settings.base_host}")
    sep_creds = (
        f"Yes (key length: {emb_settings.key_length})"
        if emb_settings.has_separate_credentials
        else "No (shares LLM credentials)"
    )
    print(f"Separate Creds:     {sep_creds}")
    print("-" * 80)

    if not do_ping:
        print("Ping:         SKIPPED (Use --ping with --check-env to test key connectivity)")
    elif not status["has_valid_key"]:
        print("Ping:         SKIPPED (No valid API key configured to ping)")
    else:
        from app.agents.client_factory import run_smoke_ping

        print("Ping:         Testing key with live smoke probe...")
        ping_res = run_smoke_ping(settings)
        if ping_res.get("success"):
            print(
                f"Ping:         OK (200 - AI gateway connection valid, host: {ping_res.get('gateway_host')}, latency: {ping_res.get('latency_ms')} ms)"
            )
        else:
            err_c = ping_res.get("error_code", "LLM_UNAVAILABLE")
            msg_c = ping_res.get("message", "Error")
            print(f"Ping:         FAILED [{err_c}]: {msg_c}")

    print("=" * 80)


def _handle_check_model(settings: LLMSettings) -> None:
    """Probes model capabilities for model-switch readiness (e.g. gateway models).

    Tests in order:
      1. Plain completion
      2. Temperature accepted?
      3. Native structured output with strict schemas (SQLAnalysis, SAPAnalysis, BusinessIntelligence)
      4. Tool / function calling
      5. JSON prompt fallback
    Prints supported modes, recommends env values, and persists to data/model_capabilities.json.
    """
    if not is_valid_api_key(settings.openai_api_key):
        print("=" * 80)
        print("                        MODEL CAPABILITY CHECK")
        print("=" * 80)
        print("Error: No valid API key configured. Cannot run live --check-model probe.")
        print("Set OPENAI_API_KEY in .env or your environment before running --check-model.")
        print("=" * 80)
        sys.exit(1)

    from app.agents.capabilities import save_capabilities
    from app.agents.client_factory import create_sync_client, get_base_host
    from app.agents.models import SAPAnalysis, SQLAnalysis
    from app.synthesizer.models import BusinessIntelligence

    client = create_sync_client(settings)
    host_only = get_base_host(settings.openai_base_url)

    print("=" * 80)
    print(f"       MODEL CAPABILITY CHECK: {settings.llm_model}")
    print(f"       AI Gateway Host: {host_only}")
    print("=" * 80)

    results: dict[str, tuple[bool, str]] = {}

    # 1. Plain Completion
    print("Testing [1/5] Plain Completion...")
    try:
        resp1 = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "Return 'OK'"}],
            max_tokens=10,
        )
        msg1 = (resp1.choices[0].message.content or "").strip()
        results["Plain Completion"] = (True, f"Received: {msg1[:50]}")
        print(f"  -> PASS (Output: {msg1[:30]})")
    except Exception as e:  # noqa: BLE001
        results["Plain Completion"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    # 2. Temperature Accepted?
    print("Testing [2/5] Temperature Accepted...")
    temp_accepted = False
    try:
        client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "Return 'OK'"}],
            max_tokens=10,
            temperature=0.0,
        )
        temp_accepted = True
        results["Temperature Accepted"] = (True, "Temperature parameter accepted")
        print("  -> PASS")
    except Exception as e:  # noqa: BLE001
        err_str = str(e).lower()
        if "temperature" in err_str and (
            "unsupported" in err_str or "unknown" in err_str or "not supported" in err_str
        ):
            results["Temperature Accepted"] = (
                False,
                "Provider does not accept temperature parameter",
            )
            print("  -> FAIL (Unsupported parameter)")
        else:
            results["Temperature Accepted"] = (False, str(e))
            print(f"  -> FAIL: {e}")

    # 3. Native Structured Output (strict schemas)
    print("Testing [3/5] Native Structured Output (strict schemas)...")
    struct_ok = False
    try:
        # Test SQLAnalysis schema
        resp_sql = client.beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {
                    "role": "user",
                    "content": "Analyze: SELECT 1. Output JSON adhering to SQLAnalysis.",
                }
            ],
            response_format=SQLAnalysis,
        )
        parsed_sql = resp_sql.choices[0].message.parsed
        # Test SAPAnalysis schema
        resp_sap = client.beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {
                    "role": "user",
                    "content": "Analyze table MARA. Output JSON adhering to SAPAnalysis.",
                }
            ],
            response_format=SAPAnalysis,
        )
        parsed_sap = resp_sap.choices[0].message.parsed
        # Test BusinessIntelligence schema
        resp_bi = client.beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {
                    "role": "user",
                    "content": "Synthesize BI for SELECT 1. Output JSON adhering to BusinessIntelligence.",
                }
            ],
            response_format=BusinessIntelligence,
        )
        parsed_bi = resp_bi.choices[0].message.parsed

        if (
            isinstance(parsed_sql, SQLAnalysis)
            and isinstance(parsed_sap, SAPAnalysis)
            and isinstance(parsed_bi, BusinessIntelligence)
        ):
            struct_ok = True
            results["Native Structured Output"] = (
                True,
                "All 3 schemas (SQL, SAP, BI) parsed strictly",
            )
            print("  -> PASS")
        else:
            results["Native Structured Output"] = (
                False,
                "One or more schemas failed to parse strictly",
            )
            print("  -> FAIL (Parse incomplete)")
    except Exception as e:  # noqa: BLE001
        results["Native Structured Output"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    # 4. Tool Calling
    print("Testing [4/5] Tool Calling...")
    tools_ok = False
    try:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "lookup_sap_table",
                    "description": "Look up an SAP table description.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "table_name": {"type": "string", "description": "SAP table name"},
                        },
                        "required": ["table_name"],
                    },
                },
            }
        ]
        resp_tools = client.chat.completions.create(  # type: ignore[call-overload]
            model=settings.llm_model,
            messages=[
                {"role": "user", "content": "Look up table MARC using the lookup_sap_table tool."}
            ],
            tools=tools,  # type: ignore[arg-type]
            tool_choice="auto",
        )
        t_calls = resp_tools.choices[0].message.tool_calls
        if t_calls and len(t_calls) > 0 and t_calls[0].function.name == "lookup_sap_table":
            tools_ok = True
            results["Tool Calling"] = (True, f"Invoked tool: {t_calls[0].function.name}")
            print("  -> PASS")
        else:
            results["Tool Calling"] = (False, "No function tool call generated")
            print("  -> FAIL (No tool call generated)")
    except Exception as e:  # noqa: BLE001
        results["Tool Calling"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    # 5. JSON Prompt Fallback
    print("Testing [5/5] JSON Prompt Fallback...")
    json_prompt_ok = False
    try:
        resp_json = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a database analyzer. Always output valid JSON object matching SQLAnalysis schema.",
                },
                {
                    "role": "user",
                    "content": (
                        "Analyze: SELECT 1. Respond with a JSON object containing keys: "
                        "sql_valid (bool), syntax_dialect (str), summary (str), "
                        "identified_patterns (list), opportunity_query (str), "
                        "error_query (str), parser_differences (list), tables (list), columns (list)."
                    ),
                },
            ],
            response_format={"type": "json_object"},
        )
        raw_json = resp_json.choices[0].message.content or "{}"
        parsed_dict = json.loads(raw_json)
        _ = SQLAnalysis.model_validate(parsed_dict)
        json_prompt_ok = True
        results["JSON Prompt Fallback"] = (True, "Valid JSON object returned and validated")
        print("  -> PASS")
    except Exception as e:  # noqa: BLE001
        results["JSON Prompt Fallback"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    # 6. Embeddings Probe
    print("Testing [6/6] Embeddings Probe...")
    from app.embeddings.client import create_embedding_sync_client
    from app.embeddings.settings import get_embedding_settings

    emb_settings = get_embedding_settings()
    emb_client = create_embedding_sync_client(emb_settings)
    emb_reachable = False
    emb_dims_returned = None
    emb_dims_accepted = False
    batch_2_works = False
    emb_latency_ms = 0.0
    emb_tokens = 0

    try:
        t0_emb = time.perf_counter()
        resp_emb = emb_client.embeddings.create(
            model=emb_settings.embedding_model,
            input=["test input 1", "test input 2"],
            encoding_format="float",
        )
        emb_latency_ms = round((time.perf_counter() - t0_emb) * 1000, 2)
        if len(resp_emb.data) == 2:
            emb_reachable = True
            batch_2_works = True
            emb_dims_returned = len(resp_emb.data[0].embedding)
            if resp_emb.usage:
                emb_tokens = resp_emb.usage.total_tokens or resp_emb.usage.prompt_tokens or 0
            results["Embeddings Reachable"] = (
                True,
                f"{emb_dims_returned} dims, {emb_latency_ms}ms, {emb_tokens} tokens",
            )
            print(f"  -> PASS (Model {emb_settings.embedding_model}: {emb_dims_returned} dims in {emb_latency_ms}ms)")
    except Exception as e:  # noqa: BLE001
        results["Embeddings Reachable"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    try:
        _ = emb_client.embeddings.create(
            model=emb_settings.embedding_model,
            input=["test dimension param"],
            dimensions=emb_dims_returned or 512,
            encoding_format="float",
        )
        emb_dims_accepted = True
        results["Embeddings Dimensions Param"] = (True, "dimensions parameter accepted")
    except Exception as e:  # noqa: BLE001
        emb_dims_accepted = False
        results["Embeddings Dimensions Param"] = (False, f"Not accepted: {e}")

    # Summary and Recommendations
    print("=" * 80)
    print("                           SUMMARY & RECOMMENDATIONS")
    print("=" * 80)
    for test_name, (passed, details) in results.items():
        status_str = "PASS" if passed else "FAIL"
        print(f"{test_name:30} : [{status_str}] ({details})")
    print("-" * 80)

    rec_struct = "native" if struct_ok else ("json_prompt" if json_prompt_ok else "unsupported")
    rec_tools = "native" if tools_ok else "prefetch"
    rec_api_mode = (
        "responses"
        if tools_ok and struct_ok and not settings.openai_base_url
        else "chat_completions"
    )

    # Save to data/model_capabilities.json
    caps_data = {
        "plain_completion": results.get("Plain Completion", (False, ""))[0],
        "temperature_supported": temp_accepted,
        "native_structured_output": struct_ok,
        "tool_calling": tools_ok,
        "json_prompt_fallback": json_prompt_ok,
        "recommended_structured_output_mode": rec_struct,
        "recommended_tools_mode": rec_tools,
        "recommended_api_mode": rec_api_mode,
        "embeddings": {
            "model": emb_settings.embedding_model,
            "reachable": emb_reachable,
            "dimensions_returned": emb_dims_returned,
            "dimensions_param_accepted": emb_dims_accepted,
            "batch_of_2_works": batch_2_works,
            "latency_ms": emb_latency_ms,
            "token_usage": emb_tokens,
        },
    }
    save_capabilities(settings.llm_model, settings.openai_base_url, caps_data)

    print("Recommended Environment Configuration:")
    print(f"  LLM_MODEL={settings.llm_model}")
    if settings.openai_base_url:
        print(f"  OPENAI_BASE_URL={settings.openai_base_url}")
    print(f"  STRUCTURED_OUTPUT_MODE={rec_struct}")
    print(f"  TOOLS_MODE={rec_tools}")
    print(f"  LLM_API_MODE={rec_api_mode}")
    print(f"  EMBEDDING_MODEL={emb_settings.embedding_model}")
    if emb_dims_returned:
        print(f"  EMBEDDING_DIMENSIONS={emb_dims_returned}")
    print("=" * 80)


def _print_summary_card(result: AgentsResult) -> None:
    """Pretty-prints a one-screen CLI summary card."""
    print("=" * 80)
    print("                           AGENTS ANALYSIS SUMMARY")
    print("=" * 80)
    print(f"Request ID:          {result.request_id}")
    print(f"Degraded:            {result.degraded}")
    print(f"Total Latency:       {result.total_latency_ms:.2f} ms")
    print("-" * 80)

    # SQL Agent Summary
    sql = result.sql
    print(f"SQL Semantics Agent: [{sql.status.upper()}]")
    print(f"  Model:             {sql.model}")
    print(f"  Latency:           {sql.latency_ms:.2f} ms (Attempts: {sql.attempts})")
    print(
        f"  Tokens:            In: {sql.usage.input_tokens}, "
        f"Out: {sql.usage.output_tokens}, Total: {sql.usage.total_tokens}"
    )
    tool_count = len(sql.tool_calls)
    tool_summary = ", ".join(tc.name for tc in sql.tool_calls) if tool_count else "None"
    print(f"  Tool Calls ({tool_count}):    {tool_summary}")
    if sql.error:
        print(f"  Error:             {sql.error.type}: {sql.error.message}")
    print("-" * 80)

    # SAP Agent Summary
    sap = result.sap
    print(f"SAP Domain Agent:    [{sap.status.upper()}]")
    print(f"  Model:             {sap.model}")
    print(f"  Latency:           {sap.latency_ms:.2f} ms (Attempts: {sap.attempts})")
    print(
        f"  Tokens:            In: {sap.usage.input_tokens}, "
        f"Out: {sap.usage.output_tokens}, Total: {sap.usage.total_tokens}"
    )
    sap_tool_count = len(sap.tool_calls)
    sap_tool_summary = ", ".join(tc.name for tc in sap.tool_calls) if sap_tool_count else "None"
    print(f"  Tool Calls ({sap_tool_count}):    {sap_tool_summary}")
    if sap.error:
        print(f"  Error:             {sap.error.type}: {sap.error.message}")

    # Guardrails Summary
    if result.guardrails:
        print("-" * 80)
        gr = result.guardrails
        status_tag = "BLOCKED" if gr.blocked else "PASSED"
        print(
            f"Guardrails Status:   [{status_tag}] (Highest Severity: {gr.highest_severity or 'None'})"
        )
        print(f"  Actions:           {gr.counts_by_action or 'None'}")
        print(f"  Events ({len(gr.events)}):")
        for ev in gr.events:
            print(f"    - [{ev.action.upper()}] ({ev.stage}/{ev.name}): {ev.message}")
    print("=" * 80)


def main() -> None:
    """CLI entry point for app.agents."""
    parser = argparse.ArgumentParser(description="Run Syniti SQL and SAP analysis agents.")
    parser.add_argument(
        "--example",
        type=str,
        help="Run against an example from DQ_Queries.xlsx / examples.json (e.g. 'sheet1', '0')",
    )
    parser.add_argument("--sql", type=str, help="Raw T-SQL query string to analyze")
    parser.add_argument("--file", type=str, help="Path to a .sql file to analyze")
    parser.add_argument(
        "--pack-only",
        action="store_true",
        help="show what will be sent to the model (no LLM call, diagnostic only)",
    )
    parser.add_argument(
        "--check-env",
        action="store_true",
        help="Print environment & config diagnostics (.env path, key source, model, api mode)",
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="When used with --check-env, perform a 1-token live ping to test the key and report HTTP status",
    )
    parser.add_argument(
        "--check-model",
        action="store_true",
        help="Run model switch readiness check: probes plain completion, native structured output, tools, and json fallback",
    )

    args = parser.parse_args()

    # Load settings (fail-fast if canonical and alias conflict)
    try:
        settings = LLMSettings()
    except ValueError as exc:
        if "Conflicting settings" in str(exc):
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        raise

    # Model switch capability check mode
    if args.check_model:
        _handle_check_model(settings)
        return

    # Diagnostics check-env mode
    if args.check_env:
        _handle_check_env(settings, do_ping=args.ping)
        return

    # Determine input SQL
    sql_text = ""
    source_desc = "inline SQL"
    if args.example:
        source_desc, sql_text = _load_example_sql(args.example)
    elif args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"Error: SQL file not found: {file_path}", file=sys.stderr)
            sys.exit(1)
        sql_text = file_path.read_text(encoding="utf-8")
        source_desc = str(file_path)
    elif args.sql:
        sql_text = args.sql
    else:
        # Default to sheet1 example
        source_desc, sql_text = _load_example_sql("sheet1")

    # Run pre-flight guardrail pipeline
    try:
        sanitized = pre_flight_pipeline(sql_text)
        sql_to_parse = sanitized.sanitized_sql
        canary_token = sanitized.canary_token
    except GuardrailBlocked as g_exc:
        print(f"Error: Guardrail blocked input: {g_exc.message}", file=sys.stderr)
        if g_exc.report:
            for ev in g_exc.report.events:
                print(f"  - [{ev.action.upper()}] ({ev.name}): {ev.message}", file=sys.stderr)
        sys.exit(1)

    # Parse query with Phase 1 parser
    sql_parser = SqlParser()
    parsed = sql_parser.parse(sql_to_parse)

    # Fail-fast check if no valid key is configured (unless diagnostic --pack-only is requested)
    if not args.pack_only and not is_valid_api_key(settings.openai_api_key):
        status = get_key_status(settings)
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
            "Error: No valid OpenAI API key is configured.\n\n"
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

    # Initialize shared knowledge dependencies
    store = KnowledgeStore()
    sap_retriever = SapRetriever()
    sql_retriever = SqlDocRetriever()

    if args.pack_only:
        sql_pack = build_sql_pack(parsed, store, max_items=settings.pack_max_items)
        sap_pack = build_sap_pack(parsed, store, max_items=settings.pack_max_items)
        output = {
            "source": source_desc,
            "sql_pack": sql_pack.model_dump(),
            "sap_pack": sap_pack.model_dump(),
        }
        print(json.dumps(output, indent=2))
        return

    def deps_factory() -> tuple[AgentDeps, AgentDeps]:
        sql_deps = AgentDeps(
            store=store,
            sap_retriever=sap_retriever,
            sql_retriever=sql_retriever,
            settings=settings,
            request_id="",
        )
        sap_deps = AgentDeps(
            store=store,
            sap_retriever=sap_retriever,
            sql_retriever=sql_retriever,
            settings=settings,
            request_id="",
        )
        return sql_deps, sap_deps

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    result = asyncio.run(
        analyze_with_agents(
            parsed=parsed,
            deps_factory=deps_factory,
            settings=settings,
            model=None,
            canary_token=canary_token,
        )
    )

    # Print JSON output
    print(result.model_dump_json(indent=2))
    print()
    # Print summary card
    _print_summary_card(result)


if __name__ == "__main__":
    main()
