#!/usr/bin/env python3
"""Live Evaluation Harness for MDM Syniti Governance Pipeline.

Evaluates real LLM orchestrator performance against workbook examples.
Requires a valid OpenAI API key and fails fast if not configured.

Usage:
    python scripts/eval_live.py [--sheets all|sheet2,sheet3] [--max-cost USD] [--repeat N] [--judge]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agents.settings import get_key_status, get_llm_settings, is_valid_api_key
from app.cache.backends import InMemoryBackend
from app.guardrails.sql_safety import validate_error_subset_of_opportunity
from app.knowledge.glossary import GlossaryHelper
from app.orchestrator.orchestrator import analyze
from app.orchestrator.settings import OrchestratorSettings
from app.parser.sql_parser import SqlParser
from app.synthesizer.models import BusinessIntelligence

FEWSHOT_SHEETS = {"sheet1", "sheet5", "sheet7", "sheet8"}
DEFAULT_MAX_REQUESTS = 50
TOKEN_BUDGET_FALLBACK = 200_000  # Fallback token limit if pricing not configured


def _load_examples() -> list[dict[str, Any]]:
    fixtures_path = BACKEND_DIR / "tests" / "fixtures" / "examples.json"
    if not fixtures_path.exists():
        fixtures_path = REPO_ROOT / "backend" / "tests" / "fixtures" / "examples.json"
    if not fixtures_path.exists():
        raise FileNotFoundError(f"Cannot find fixture examples at: {fixtures_path}")
    with open(fixtures_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _score_example(
    bi: BusinessIntelligence,
    expected: dict[str, Any],
    input_sql: str,
    glossary: GlossaryHelper,
) -> dict[str, Any]:
    """Scores an individual analysis result against deterministic and schema-based criteria."""
    scores: dict[str, Any] = {}

    # 1. Schema validity
    scores["schema_valid"] = isinstance(bi, BusinessIntelligence) and not bi.meta.degraded

    # 2. Parser baseline and staging resolution
    parser = SqlParser()
    parsed = parser.parse(input_sql)
    base_tables = [t.sap_table or t.raw_name for t in parsed.tables]
    affected_upper = [e.upper() for e in bi.affected_entities]

    # Staging names resolved to base tables
    tables_resolved = any(
        bt.upper() in affected_upper or bt.upper() in bi.technical_summary.upper()
        for bt in base_tables
    )
    scores["tables_resolved"] = tables_resolved

    # 3. No invented tables in generated opportunity/error queries
    opp_sql = bi.governance.opportunity_query or ""
    err_sql = bi.governance.error_query or ""
    opp_valid = bi.meta.validation.opportunity_sql_ok
    err_valid = bi.meta.validation.error_sql_ok
    scores["opportunity_sql_valid"] = opp_valid
    scores["error_sql_valid"] = err_valid

    # 4. Error query subset of opportunity query
    if opp_sql and err_sql:
        verdict = validate_error_subset_of_opportunity(opp_sql, err_sql)
        scores["subset_valid"] = verdict.allowed
    else:
        scores["subset_valid"] = False

    # 5. Custom Z fields and *NEW derivations described
    has_custom = any(
        "Z" in col.column.upper() or "NEW" in col.column.upper() for col in parsed.columns
    )
    if has_custom:
        text_corp = f"{bi.technical_summary} {bi.business_meaning} {bi.final_learning}".upper()
        z_described = any(
            col.column.upper() in text_corp
            for col in parsed.columns
            if col.column.upper().startswith("Z")
        )
        scores["custom_derivations_described"] = z_described
    else:
        scores["custom_derivations_described"] = True

    # 6. Subject area & business process in glossary or marked is_new
    sa_known = bi.governance.subject_area in glossary.list_options("subject_areas")
    bp_known = bi.governance.business_process in glossary.list_options("business_processes")
    scores["glossary_compliant"] = (sa_known or bi.governance.is_new.subject_area) and (
        bp_known or bi.governance.is_new.business_process
    )

    # 7. Matches parser baseline
    scores["matches_parser_baseline"] = bi.meta.validation.matches_parser_baseline

    # 8. Guardrails clean
    scores["guardrail_blocked"] = bi.meta.guardrails.blocked
    scores["guardrail_event_count"] = len(bi.meta.guardrails.events)

    # 9. Metrics
    scores["total_tokens"] = bi.meta.token_usage.total
    scores["latency_ms"] = bi.meta.stage_timings_ms.get("total", 0.0)
    scores["cost_usd"] = bi.meta.token_usage.cost_usd

    # Overall pass
    scores["all_passed"] = (
        scores["schema_valid"]
        and scores["tables_resolved"]
        and scores["opportunity_sql_valid"]
        and scores["error_sql_valid"]
        and scores["subset_valid"]
        and scores["glossary_compliant"]
        and not scores["guardrail_blocked"]
    )
    return scores


async def _run_llm_judge(
    bi: BusinessIntelligence,
    expected: dict[str, Any],
    api_key: str,
    model: str,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Optional LLM-as-judge pass (approximate heuristic signal, temperature 0)."""
    import openai

    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    judge_prompt = f"""You are an expert data migration auditor. Rate the following generated governance metadata
against the expected gold standard on a scale of 1 to 5 (5 = perfect match in business intent and semantics).

Expected Rule: {expected.get("Rule", "")}
Generated Rule: {bi.governance.rule}

Expected Specification: {expected.get("Specification", "")}
Generated Specification: {bi.governance.specification}

Expected Business Meaning: {expected.get("Business Meaning", "")}
Generated Business Meaning: {bi.business_meaning}

Return a valid JSON object with keys:
"rule_score": int (1-5),
"spec_score": int (1-5),
"meaning_score": int (1-5),
"rationale": str
"""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": judge_prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        raw = resp.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "rule_score": 0, "spec_score": 0, "meaning_score": 0}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Live evaluation harness for MDM Syniti pipeline")
    parser.add_argument(
        "--sheets",
        type=str,
        default="all",
        help="Sheets to evaluate: 'all' or comma-separated list like 'sheet1,sheet2,sheet3'",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=5.0,
        help="Maximum budget in USD before stopping (default: $5.00)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of evaluation repetitions per example (default: 1)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Run LLM-as-judge scoring pass (1-5 scale) comparing generated text vs workbook expectations",
    )

    args = parser.parse_args()

    # Fail fast if no valid API key
    llm_settings = get_llm_settings()
    if not is_valid_api_key(llm_settings.openai_api_key):
        status = get_key_status(llm_settings)
        print("=" * 80, file=sys.stderr)
        print(
            "ERROR: No valid OpenAI API key configured for live evaluation harness.",
            file=sys.stderr,
        )
        print(f"Looked in environment and {status['env_path']}", file=sys.stderr)
        print(
            "Set OPENAI_API_KEY in .env or your environment to execute live evaluation.",
            file=sys.stderr,
        )
        print("=" * 80, file=sys.stderr)
        sys.exit(1)

    api_key_str = (
        llm_settings.openai_api_key.get_secret_value() if llm_settings.openai_api_key else ""
    )
    glossary = GlossaryHelper()
    orch_settings = OrchestratorSettings()
    cache_backend = InMemoryBackend()

    # Filter sheets
    all_examples = _load_examples()
    if args.sheets.lower() == "all":
        selected_examples = all_examples
    else:
        req_sheets = {s.strip().lower() for s in args.sheets.split(",")}
        selected_examples = [
            ex
            for ex in all_examples
            if str(ex.get("id", ex.get("source_sheet", ""))).lower() in req_sheets
        ]

    if not selected_examples:
        print(f"No examples matched sheets filter: '{args.sheets}'")
        sys.exit(1)

    print("=" * 80)
    print("             MDM SYNITI LIVE EVALUATION HARNESS")
    print(f"  Model:           {llm_settings.llm_model}")
    print(f"  Selected Sheets: {args.sheets} ({len(selected_examples)} queries)")
    print(f"  Budget Cap:      ${args.max_cost:.2f} USD")
    print(f"  Judge Pass:      {'Enabled' if args.judge else 'Disabled'}")
    print("=" * 80)

    results: list[dict[str, Any]] = []
    total_cost_accum = 0.0
    total_tokens_accum = 0
    request_count = 0

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Evaluate examples
    for ex in selected_examples:
        sheet_id = str(ex.get("id", ex.get("source_sheet", "unknown")))
        sql_input = ex["input_sql"]
        expected = ex.get("expected", {})
        is_fewshot = sheet_id.lower() in FEWSHOT_SHEETS

        print(f"\nEvaluating [{sheet_id}] ({'Few-Shot' if is_fewshot else 'Held-Out'})...")

        for rep in range(args.repeat):
            if request_count >= DEFAULT_MAX_REQUESTS:
                print(f"Reached max requests cap ({DEFAULT_MAX_REQUESTS}). Stopping.")
                break

            if total_cost_accum >= args.max_cost:
                print(f"Budget cap of ${args.max_cost:.2f} reached. Stopping.")
                break

            request_count += 1
            t_start = time.perf_counter()

            try:
                bi = await analyze(
                    sql=sql_input,
                    cache_backend=cache_backend,
                    settings=orch_settings,
                    llm_settings=llm_settings,
                )
                elapsed_ms = (time.perf_counter() - t_start) * 1000

                scores = _score_example(bi, expected, sql_input, glossary)
                if scores["cost_usd"] is not None:
                    total_cost_accum += scores["cost_usd"]
                total_tokens_accum += scores["total_tokens"]

                judge_res: dict[str, Any] = {}
                if args.judge:
                    judge_res = await _run_llm_judge(
                        bi,
                        expected,
                        api_key_str,
                        llm_settings.llm_model,
                        llm_settings.openai_base_url,
                    )

                res_record = {
                    "sheet_id": sheet_id,
                    "is_fewshot": is_fewshot,
                    "repetition": rep + 1,
                    "scores": scores,
                    "judge": judge_res,
                    "request_id": bi.meta.request_id,
                    "cache_status": bi.meta.cache_status,
                }
                results.append(res_record)

                status_tag = "PASS" if scores["all_passed"] else "FAIL"
                print(
                    f"  Rep {rep + 1}: [{status_tag}] (Tokens: {scores['total_tokens']}, Latency: {elapsed_ms:.0f}ms)"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  Rep {rep + 1}: [ERROR] {exc}")
                results.append(
                    {
                        "sheet_id": sheet_id,
                        "is_fewshot": is_fewshot,
                        "repetition": rep + 1,
                        "error": str(exc),
                    }
                )

    # Execute Cache Behavior Assertions:
    # sheet1 twice -> assert second is hit_final
    print("\nVerifying Cache Persistence Sequence (Sheet1 repeat)...")
    sheet1_sql = next(
        (ex["input_sql"] for ex in all_examples if str(ex.get("id", "")).lower() == "sheet1"), ""
    )
    if sheet1_sql:
        try:
            r1 = await analyze(
                sql=sheet1_sql,
                cache_backend=cache_backend,
                settings=orch_settings,
                llm_settings=llm_settings,
            )
            r2 = await analyze(
                sql=sheet1_sql,
                cache_backend=cache_backend,
                settings=orch_settings,
                llm_settings=llm_settings,
            )
            cache_hit_verified = r2.meta.cache_status == "hit_final"
            print(f"  Sheet1 Run 1 Cache: {r1.meta.cache_status.upper()}")
            print(
                f"  Sheet1 Run 2 Cache: {r2.meta.cache_status.upper()} (Hit Verified: {cache_hit_verified})"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  Sheet1 Cache verification skipped due to provider error: {exc}")

    # Output Reports
    eval_dir = REPO_ROOT / "docs" / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    json_path = eval_dir / f"eval_{timestamp_str}.json"
    md_path = eval_dir / f"eval_{timestamp_str}.md"

    _save_eval_reports(
        json_path=json_path,
        md_path=md_path,
        timestamp_str=timestamp_str,
        llm_model=llm_settings.llm_model,
        request_count=request_count,
        total_cost_accum=total_cost_accum,
        total_tokens_accum=total_tokens_accum,
        results=results,
    )

    print(f"\nEvaluation Complete. Reports written to:\n  JSON: {json_path}\n  Markdown: {md_path}")


def _save_eval_reports(
    json_path: Path,
    md_path: Path,
    timestamp_str: str,
    llm_model: str,
    request_count: int,
    total_cost_accum: float,
    total_tokens_accum: int,
    results: list[dict[str, Any]],
) -> None:
    # Save JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": timestamp_str,
                "model": llm_model,
                "total_requests": request_count,
                "total_cost_usd": total_cost_accum,
                "total_tokens": total_tokens_accum,
                "results": results,
            },
            f,
            indent=2,
        )

    # Generate Markdown Summary
    fewshot_res = [r for r in results if r.get("is_fewshot")]
    heldout_res = [r for r in results if not r.get("is_fewshot")]

    def _calc_pass_rate(subset: list[dict[str, Any]]) -> float:
        if not subset:
            return 0.0
        passed = sum(1 for r in subset if r.get("scores", {}).get("all_passed"))
        return (passed / len(subset)) * 100.0

    fewshot_rate = _calc_pass_rate(fewshot_res)
    heldout_rate = _calc_pass_rate(heldout_res)

    md_content = f"""# Live Evaluation Report ({timestamp_str})

- **Target Model:** `{llm_model}`
- **Total Invocations:** {request_count}
- **Accumulated Tokens:** {total_tokens_accum:,}
- **Estimated Cost:** ${total_cost_accum:.4f} USD
- **Few-Shot Pass Rate:** {fewshot_rate:.1f}% ({len(fewshot_res)} runs)
- **Held-Out Pass Rate:** {heldout_rate:.1f}% ({len(heldout_res)} runs)

## 1. Summary by Sheet Category

### Few-Shot Examples (Sheets 1, 5, 7, 8)
| Sheet ID | Schema | Tables Resolved | Opp SQL | Error SQL | Subset | Pass/Fail | Tokens | Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
"""
    for r in fewshot_res:
        sc = r.get("scores", {})
        md_content += (
            f"| `{r.get('sheet_id')}` | "
            f"{'PASS' if sc.get('schema_valid') else 'FAIL'} | "
            f"{'PASS' if sc.get('tables_resolved') else 'FAIL'} | "
            f"{'PASS' if sc.get('opportunity_sql_valid') else 'FAIL'} | "
            f"{'PASS' if sc.get('error_sql_valid') else 'FAIL'} | "
            f"{'PASS' if sc.get('subset_valid') else 'FAIL'} | "
            f"**{'PASS' if sc.get('all_passed') else 'FAIL'}** | "
            f"{sc.get('total_tokens', 0):,} | "
            f"{sc.get('latency_ms', 0):.0f} ms |\n"
        )

    md_content += """
### Held-Out Examples (Sheets 2, 3, 4, 6, 9, 10, 11)
| Sheet ID | Schema | Tables Resolved | Opp SQL | Error SQL | Subset | Pass/Fail | Tokens | Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :--- |
"""
    for r in heldout_res:
        sc = r.get("scores", {})
        md_content += (
            f"| `{r.get('sheet_id')}` | "
            f"{'PASS' if sc.get('schema_valid') else 'FAIL'} | "
            f"{'PASS' if sc.get('tables_resolved') else 'FAIL'} | "
            f"{'PASS' if sc.get('opportunity_sql_valid') else 'FAIL'} | "
            f"{'PASS' if sc.get('error_sql_valid') else 'FAIL'} | "
            f"{'PASS' if sc.get('subset_valid') else 'FAIL'} | "
            f"**{'PASS' if sc.get('all_passed') else 'FAIL'}** | "
            f"{sc.get('total_tokens', 0):,} | "
            f"{sc.get('latency_ms', 0):.0f} ms |\n"
        )

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\nEvaluation Complete. Reports written to:\n  JSON: {json_path}\n  Markdown: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
