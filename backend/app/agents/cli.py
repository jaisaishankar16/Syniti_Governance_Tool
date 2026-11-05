"""CLI interface for running SQLSemanticsAgent and SAPDomainAgent."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from app.agents.knowledge_pack import build_sap_pack, build_sql_pack
from app.agents.models import AgentDeps, AgentsResult
from app.agents.parallel import analyze_with_agents
from app.agents.settings import (
    ENV_FILE_PATH,
    LLMSettings,
    get_key_status,
    get_llm_settings,
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
    """Inspects environment, .env file path and status, and optionally pings OpenAI."""
    status = get_key_status(settings)
    env_path = status["env_path"]
    if status["env_exists"]:
        env_state = "Empty" if status["env_is_empty"] else "Populated"
        env_desc = f"{env_path} (Status: Found, {env_state})"
    else:
        env_desc = f"{env_path} (Status: Not found)"

    key_src = status["key_source"] or "Not configured"
    if status["has_valid_key"]:
        key_desc = (
            f"Configured (Length: {status['key_length']}, First 7: '{status['key_prefix']}...')"
        )
    else:
        key_desc = "Not set (or invalid placeholder)"

    print("=" * 80)
    print("                        ENVIRONMENT & CONFIG CHECK")
    print("=" * 80)
    print(f".env Path:    {env_desc}")
    print(f"Key Source:   {key_src}")
    print(f"API Key:      {key_desc}")
    print(f"Model:        {settings.llm_model}")
    print(
        f"Base URL:     {settings.openai_base_url or 'None (default: https://api.openai.com/v1)'}"
    )
    print(f"API Mode:     {settings.llm_api_mode}")
    print(f"Temperature:  {settings.llm_temperature}")
    print(f"Max Turns:    {settings.llm_max_turns}")
    print(f"Timeout:      {settings.llm_timeout_s}s")
    print("-" * 80)

    if not do_ping:
        print("Ping:         SKIPPED (Use --ping with --check-env to test key connectivity)")
    else:
        if not status["has_valid_key"]:
            print("Ping:         SKIPPED (No valid API key configured to ping)")
        else:
            print("Ping:         Testing key with live completion...")
            try:
                import openai

                client = openai.OpenAI(
                    api_key=settings.openai_api_key.get_secret_value()
                    if settings.openai_api_key
                    else None,
                    base_url=settings.openai_base_url,
                    timeout=10.0,
                )
                client.chat.completions.create(
                    model=settings.llm_model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                )
                print("Ping:         OK (200 - Connection and API key valid)")
            except openai.AuthenticationError as e:
                print(f"Ping:         401 (bad key: {e})")
            except openai.RateLimitError as e:
                print(f"Ping:         429 (quota/billing or rate limit exceeded: {e})")
            except (openai.APIConnectionError, openai.APITimeoutError) as e:
                print(f"Ping:         Network error ({type(e).__name__}: {e})")
            except Exception as e:  # noqa: BLE001  # noqa: BLE001
                print(f"Ping:         Error ({type(e).__name__}: {e})")

    print("=" * 80)


def _handle_check_model(settings: LLMSettings) -> None:
    """Probes model capabilities for model-switch readiness (e.g. Astra-Luna or alternative models).

    Tests in order:
      1. Plain completion
      2. Native structured output with strict schemas (SQLAnalysis, SAPAnalysis, BusinessIntelligence)
      3. Tool / function calling
      4. JSON prompt fallback
    Prints supported modes and recommends env values.
    """
    if not is_valid_api_key(settings.openai_api_key):
        print("=" * 80)
        print("                        MODEL CAPABILITY CHECK")
        print("=" * 80)
        print("Error: No valid API key configured. Cannot run live --check-model probe.")
        print("Set OPENAI_API_KEY in .env or your environment before running --check-model.")
        print("=" * 80)
        sys.exit(1)

    import openai

    from app.agents.models import SQLAnalysis

    api_key_str = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    client = openai.OpenAI(
        api_key=api_key_str,
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_s,
    )

    print("=" * 80)
    print(f"       MODEL CAPABILITY CHECK: {settings.llm_model}")
    print(f"       Endpoint: {settings.openai_base_url or 'https://api.openai.com/v1'}")
    print("=" * 80)

    results: dict[str, tuple[bool, str]] = {}

    # 1. Plain Completion
    print("Testing [1/4] Plain Completion...")
    try:
        resp1 = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": "Return 'OK'"}],
            max_tokens=10,
            temperature=0.0,
        )
        msg1 = resp1.choices[0].message.content or ""
        results["Plain Completion"] = (True, f"Received: {msg1.strip()[:50]}")
        print("  -> PASS")
    except Exception as e:  # noqa: BLE001
        results["Plain Completion"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    # 2. Native Structured Output (strict schema)
    print("Testing [2/4] Native Structured Output (strict schema)...")
    try:
        resp2 = client.beta.chat.completions.parse(
            model=settings.llm_model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyze this simple SQL: SELECT 1 AS id. "
                        "Return valid JSON matching the strict schema."
                    ),
                }
            ],
            response_format=SQLAnalysis,
        )
        parsed2 = resp2.choices[0].message.parsed
        if parsed2 and isinstance(parsed2, SQLAnalysis):
            results["Native Structured Output"] = (
                True,
                "Strict JSON schema successfully parsed",
            )
            print("  -> PASS")
        else:
            results["Native Structured Output"] = (
                False,
                "Response did not parse into SQLAnalysis",
            )
            print("  -> FAIL")
    except Exception as e:  # noqa: BLE001
        results["Native Structured Output"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    # 3. Tool Calling
    print("Testing [3/4] Tool Calling...")
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
                            "table_name": {
                                "type": "string",
                                "description": "SAP table name",
                            },
                        },
                        "required": ["table_name"],
                    },
                },
            }
        ]
        resp3 = client.chat.completions.create(  # type: ignore[call-overload]
            model=settings.llm_model,
            messages=[
                {
                    "role": "user",
                    "content": "Look up table MARC using the lookup_sap_table tool.",
                }
            ],
            tools=tools,  # type: ignore[arg-type]
            tool_choice="auto",
        )
        tool_calls = resp3.choices[0].message.tool_calls
        if tool_calls and len(tool_calls) > 0 and tool_calls[0].function.name == "lookup_sap_table":
            results["Tool Calling"] = (
                True,
                f"Invoked tool: {tool_calls[0].function.name}",
            )
            print("  -> PASS")
        else:
            results["Tool Calling"] = (False, "No function tool call generated")
            print("  -> FAIL")
    except Exception as e:  # noqa: BLE001
        results["Tool Calling"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    # 4. JSON Prompt Fallback
    print("Testing [4/4] JSON Prompt Fallback...")
    try:
        resp4 = client.chat.completions.create(
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
            temperature=0.0,
        )
        raw_json = resp4.choices[0].message.content or "{}"
        _ = json.loads(raw_json)
        results["JSON Prompt Fallback"] = (True, "Valid JSON object returned")
        print("  -> PASS")
    except Exception as e:  # noqa: BLE001
        results["JSON Prompt Fallback"] = (False, str(e))
        print(f"  -> FAIL: {e}")

    # Recommendations
    print("=" * 80)
    print("                           SUMMARY & RECOMMENDATIONS")
    print("=" * 80)
    for test_name, (passed, details) in results.items():
        status_str = "PASS" if passed else "FAIL"
        print(f"{test_name:30} : [{status_str}] ({details})")
    print("-" * 80)

    native_ok = results.get("Native Structured Output", (False, ""))[0]
    tools_ok = results.get("Tool Calling", (False, ""))[0]
    json_prompt_ok = results.get("JSON Prompt Fallback", (False, ""))[0]

    rec_struct = "native" if native_ok else ("json_prompt" if json_prompt_ok else "unsupported")
    rec_api_mode = "responses" if tools_ok and native_ok else "chat_completions"

    print("Recommended Environment Configuration:")
    print(f"  LLM_MODEL={settings.llm_model}")
    if settings.openai_base_url:
        print(f"  OPENAI_BASE_URL={settings.openai_base_url}")
    print(f"  STRUCTURED_OUTPUT_MODE={rec_struct}")
    print(f"  LLM_API_MODE={rec_api_mode}")
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

    # Load settings
    settings = get_llm_settings()

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
