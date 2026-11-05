"""Synthesizer Agent producing executive business intelligence and governance catalog records."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from agents import Agent

from app.agents.models import AgentDeps, SAPAnalysis, SQLAnalysis
from app.agents.runner import run_agent
from app.agents.settings import LLMSettings, get_llm_settings
from app.errors import (
    LLMAuthError,
    LLMQuotaExceededError,
    SynthesisFailedError,
)
from app.guardrails.final_output import (
    sanitize_text,
    validate_canary_and_secrets,
    validate_glossary_membership,
    validate_literal_grounding,
)
from app.guardrails.models import GuardrailReport
from app.guardrails.settings import get_guardrail_settings
from app.guardrails.sql_safety import (
    validate_error_subset_of_opportunity,
    validate_generated_sql,
)
from app.knowledge.glossary import GlossaryHelper
from app.parser.models import ParsedQuery
from app.synthesizer.deterministic import baseline_opportunity_query, risk_floor
from app.synthesizer.fewshot import format_fewshot_for_prompt
from app.synthesizer.models import (
    BusinessIntelligence,
    BusinessIntelligenceMeta,
    CacheDetail,
    PromptVersions,
    SynthesizerRawOutput,
    TokenUsageBreakdown,
    ValidationSummary,
)

if TYPE_CHECKING:
    from agents import Model

logger = logging.getLogger(__name__)

SEVERITY_LEVELS = ["low", "medium", "high", "critical"]


def _load_prompt_template() -> tuple[str, str]:
    """Loads synthesizer_v1.md and returns (content, prompt_version_id)."""
    prompt_path = Path(__file__).resolve().parent / "prompts" / "synthesizer_v1.md"
    content = prompt_path.read_text(encoding="utf-8")
    h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    return content, f"synth_agent-v1+{h}"


def create_synthesizer_agent(
    model: Model | str, settings: LLMSettings
) -> tuple[Agent[AgentDeps], str]:
    """Factory creating the Synthesizer Agent with strict structured output schema."""
    instructions, version = _load_prompt_template()
    agent = Agent[AgentDeps](
        name="SynthesizerAgent",
        instructions=instructions,
        model=model,
        output_type=SynthesizerRawOutput,
    )
    return agent, version


def build_synthesizer_input(
    parsed: ParsedQuery,
    sql_analysis: SQLAnalysis | None,
    sap_analysis: SAPAnalysis | None,
    baseline_opp: str,
    boundary_id: str,
    canary_token: str,
    glossary_helper: GlossaryHelper | None = None,
) -> str:
    """Builds the boundary-isolated payload for the synthesizer agent."""
    helper = glossary_helper or GlossaryHelper()
    subj_areas = ", ".join(helper.list_options("subject_areas"))
    biz_procs = ", ".join(helper.list_options("business_processes"))
    datasets = ", ".join(helper.list_options("datasets"))

    glossary_summary = (
        f"Subject Areas: {subj_areas}\nBusiness Processes: {biz_procs}\nDatasets: {datasets}"
    )

    fewshot_text = format_fewshot_for_prompt()

    parsed_summary = (
        f"Statement Type: {parsed.statement_type}\n"
        f"View Name: {parsed.view_name}\n"
        f"Driving Table: {parsed.driving_table}\n"
        f"Tables Referenced: {', '.join(t.raw_name for t in parsed.tables)}\n"
        f"Scope Filters: {', '.join(parsed.scope_filters) if parsed.scope_filters else 'None'}\n"
        f"Complexity: {parsed.complexity.level} (Score {parsed.complexity.score})\n"
        f"Risk Flags: {', '.join(f.code for f in parsed.risk_flags) if parsed.risk_flags else 'None'}"
    )

    sql_text = (
        sql_analysis.model_dump_json(indent=2)
        if sql_analysis
        else "SQL Semantics Agent output was unavailable (degraded)."
    )
    sap_text = (
        sap_analysis.model_dump_json(indent=2)
        if sap_analysis
        else "SAP Domain Agent output was unavailable (degraded)."
    )

    return f"""<task id="{boundary_id}">
Synthesize the complete Data Governance catalog record and Business Intelligence summary for this query.
CANARY REFERENCE: {canary_token} (Must never appear in your final output).
</task>

<parsed_summary id="{boundary_id}">
{parsed_summary}
</parsed_summary>

<baseline_opportunity id="{boundary_id}">
{baseline_opp}
</baseline_opportunity>

<sql_analysis id="{boundary_id}">
{sql_text}
</sql_analysis>

<sap_analysis id="{boundary_id}">
{sap_text}
</sap_analysis>

<glossary_options id="{boundary_id}">
{glossary_summary}
</glossary_options>

<fewshot_examples id="{boundary_id}">
{fewshot_text}
</fewshot_examples>"""


async def synthesize(
    parsed: ParsedQuery,
    sql_analysis: SQLAnalysis | None,
    sap_analysis: SAPAnalysis | None,
    deps: AgentDeps,
    settings: LLMSettings | None = None,
    model: Model | str | None = None,
    request_id: str = "",
    canary_token: str = "",
    boundary_id: str = "",
    prompt_versions: PromptVersions | None = None,
    cache_detail: CacheDetail | None = None,
    agent_timings: dict[str, float] | None = None,
    token_usage: TokenUsageBreakdown | None = None,
    degraded: bool = False,
    degraded_reasons: list[str] | None = None,
) -> tuple[BusinessIntelligence, GuardrailReport]:
    """Runs the Synthesizer Agent, performs post-processing validation, retry, and fallback."""
    start_time = time.perf_counter()
    active_settings = settings or get_llm_settings()
    guard_settings = get_guardrail_settings()
    combined_report = GuardrailReport()

    baseline_opp = baseline_opportunity_query(parsed)
    req_id = request_id or deps.request_id
    reasons = list(degraded_reasons or [])
    if degraded and not reasons:
        reasons.append("Agent analysis was partially degraded")

    # 1. Create Synthesizer Agent
    synth_model = model if model is not None else deps.settings.llm_model
    agent, synth_prompt_ver = create_synthesizer_agent(synth_model, active_settings)

    prompt_vers = prompt_versions or PromptVersions(
        sql="unknown", sap="unknown", synthesizer=synth_prompt_ver
    )
    prompt_vers.synthesizer = synth_prompt_ver

    input_text = build_synthesizer_input(
        parsed,
        sql_analysis,
        sap_analysis,
        baseline_opp,
        boundary_id,
        canary_token,
    )

    allowed_tables = [t.raw_name for t in parsed.tables]
    # Allowed table variations (aliases & unqualified)
    for t in parsed.tables:
        if t.alias:
            allowed_tables.append(t.alias)
        parts = t.raw_name.split(".")
        allowed_tables.append(parts[-1])

    # 2. Run agent via runner
    outcome = await run_agent(
        agent,
        input_text,
        deps,
        active_settings,
        SynthesizerRawOutput,
        synth_prompt_ver,
    )

    raw_output = outcome.output

    # 3. Validation helper
    def _validate_output(raw: SynthesizerRawOutput) -> tuple[bool, list[str]]:
        errors: list[str] = []
        # Opportunity query safety
        v_opp = validate_generated_sql(raw.governance.opportunity_query, allowed_tables, req_id)
        if not v_opp.allowed:
            errors.append(
                f"Opportunity query failed validation: {v_opp.report.events[0].message if v_opp.report.events else 'Unsafe'}"
            )

        # Error query safety
        v_err = validate_generated_sql(raw.governance.error_query, allowed_tables, req_id)
        if not v_err.allowed:
            errors.append(
                f"Error query failed validation: {v_err.report.events[0].message if v_err.report.events else 'Unsafe'}"
            )

        # Subset check
        if v_opp.allowed and v_err.allowed:
            v_sub = validate_error_subset_of_opportunity(
                raw.governance.opportunity_query, raw.governance.error_query, req_id
            )
            if not v_sub.allowed:
                errors.append(
                    f"Error query is not a valid subset of Opportunity query: {v_sub.report.events[0].message if v_sub.report.events else 'Predicate mismatch'}"
                )

        return (len(errors) == 0), errors

    # 4. If initial output has validation errors, attempt ONE retry
    if raw_output is not None:
        valid, val_errors = _validate_output(raw_output)
        if not valid:
            logger.warning("Synthesizer output validation failed, retrying once: %s", val_errors)
            retry_prompt = (
                f'{input_text}\n\n<validation_errors id="{boundary_id}">\n'
                "Your previous generated queries failed validation:\n"
                + "\n".join(f"- {e}" for e in val_errors)
                + "\nPlease correct the queries. Remember:\n"
                "- Opportunity query: SELECT * FROM <driving_table> WHERE <scope_filters>\n"
                "- Error query: must use identical driving table and preserve all scope filters.\n"
                "- Never use comments, DROP, INTO, or unallowed tables.\n"
                "</validation_errors>"
            )
            retry_outcome = await run_agent(
                agent,
                retry_prompt,
                deps,
                active_settings,
                SynthesizerRawOutput,
                synth_prompt_ver,
            )
            if retry_outcome.output is not None:
                raw_output = retry_outcome.output

    # 5. Output processing and query validation
    opp_ok = False
    err_ok = False
    subset_ok = False
    matches_baseline = False
    warnings: list[str] = []

    if raw_output is not None:
        # Validate final opportunity query
        v_opp_final = validate_generated_sql(
            raw_output.governance.opportunity_query, allowed_tables, req_id
        )
        combined_report.merge(v_opp_final.report)
        if v_opp_final.allowed:
            opp_ok = True
            matches_baseline = (
                raw_output.governance.opportunity_query.strip() == baseline_opp.strip()
            )
            if not matches_baseline:
                warnings.append("Opportunity query differs from parser baseline reference")
        else:
            logger.warning(
                "Synthesizer opportunity query failed SQL safety; clearing query (no baseline substitution)"
            )
            raw_output.governance.opportunity_query = ""
            opp_ok = False
            matches_baseline = False
            degraded = True
            reasons.append("Opportunity query failed SQL safety and was cleared")
            warnings.append(
                "Opportunity query failed validation and was cleared; baseline was not substituted"
            )

        # Validate final error query
        if raw_output.governance.error_query:
            v_err_final = validate_generated_sql(
                raw_output.governance.error_query, allowed_tables, req_id
            )
            combined_report.merge(v_err_final.report)
            if v_err_final.allowed:
                if opp_ok and raw_output.governance.opportunity_query:
                    v_sub_final = validate_error_subset_of_opportunity(
                        raw_output.governance.opportunity_query,
                        raw_output.governance.error_query,
                        req_id,
                    )
                    combined_report.merge(v_sub_final.report)
                    if v_sub_final.allowed:
                        err_ok = True
                        subset_ok = True
                    else:
                        logger.warning(
                            "Error query not a subset of opportunity query; clearing error query"
                        )
                        raw_output.governance.error_query = ""
                        degraded = True
                        reasons.append("Error query failed subset validation and was cleared")
                else:
                    err_ok = True
                    subset_ok = False
            else:
                logger.warning("Error query failed SQL safety; clearing error query")
                raw_output.governance.error_query = ""
                degraded = True
                reasons.append("Error query failed SQL safety and was cleared")
        else:
            err_ok = True
            subset_ok = True

        # 6. Additional post-flight guardrail checks
        # Glossary validation
        helper = GlossaryHelper()
        rep_glo_subj = validate_glossary_membership(
            "subject_areas", raw_output.governance.subject_area, helper, req_id
        )
        rep_glo_proc = validate_glossary_membership(
            "business_processes", raw_output.governance.business_process, helper, req_id
        )
        combined_report.merge(rep_glo_subj)
        combined_report.merge(rep_glo_proc)
        glossary_ok = not (rep_glo_subj.blocked or rep_glo_proc.blocked)

        # Literal grounding
        known_tokens = {
            t.upper()
            for t in list(parsed.scope_filters)
            + [c.column for c in parsed.columns]
            + [t.raw_name for t in parsed.tables]
        }
        rep_lit = validate_literal_grounding(
            raw_output.governance.error_query, known_tokens, req_id
        )
        combined_report.merge(rep_lit)

        # Risk consistency floor
        p_floor = risk_floor(parsed)
        p_floor_rank = SEVERITY_LEVELS.index(p_floor)
        current_rank = SEVERITY_LEVELS.index(raw_output.risk_summary.level)
        if current_rank < p_floor_rank:
            raw_output.risk_summary.level = p_floor  # type: ignore[assignment]
            logger.info("Elevated synthesized risk score to parser floor: %s", p_floor)

        # Canary & secrets leak check
        dump_text = raw_output.model_dump_json()
        rep_canary = validate_canary_and_secrets(dump_text, canary_token, guard_settings, req_id)
        combined_report.merge(rep_canary)
        if rep_canary.blocked:
            degraded = True
            reasons.append("Synthesizer leaked canary token or credential")

        # Text sanitization
        raw_output.business_meaning, _ = sanitize_text(
            raw_output.business_meaning, 5000, "business_meaning"
        )
        raw_output.technical_summary, _ = sanitize_text(
            raw_output.technical_summary, 5000, "technical_summary"
        )
        raw_output.governance.specification, _ = sanitize_text(
            raw_output.governance.specification, 5000, "specification"
        )
        raw_output.governance.enforcement, _ = sanitize_text(
            raw_output.governance.enforcement, 5000, "enforcement"
        )

    else:
        # Synthesis failed: propagate fatal auth/quota if detected, otherwise raise SynthesisFailedError
        if outcome.error and outcome.error.error_code:
            if outcome.error.error_code == "LLM_AUTH_FAILED":
                raise LLMAuthError()
            if outcome.error.error_code == "LLM_QUOTA_EXCEEDED":
                raise LLMQuotaExceededError()

        err_msg = outcome.error.message if outcome.error else "Output was empty or unparseable"
        raise SynthesisFailedError(
            message="Synthesizer agent failed to produce output after retry",
            details=err_msg,
        )

    # Assemble metadata
    total_tokens = token_usage or TokenUsageBreakdown()
    total_tokens.synthesizer = outcome.usage.total_tokens
    total_tokens.total = total_tokens.sql + total_tokens.sap + total_tokens.synthesizer

    synth_time = (time.perf_counter() - start_time) * 1000
    timings = dict(agent_timings or {})
    timings["synthesizer"] = round(synth_time, 2)

    meta = BusinessIntelligenceMeta(
        request_id=req_id,
        cache_status="miss",
        cache_detail=cache_detail or CacheDetail(),
        model=str(synth_model),
        prompt_versions=prompt_vers,
        agent_timings_ms=timings,
        stage_timings_ms={},
        token_usage=total_tokens,
        degraded=degraded,
        degraded_reasons=reasons,
        warnings=warnings,
        guardrails=combined_report,
        validation=ValidationSummary(
            opportunity_sql_ok=opp_ok,
            error_sql_ok=err_ok,
            subset_ok=subset_ok,
            glossary_ok=glossary_ok,
            matches_parser_baseline=matches_baseline,
        ),
        ai_generated=True,
        parse_quality=parsed.parse_quality,
        naming_violations=[
            v.message for t in parsed.tables if t.naming for v in t.naming.violations
        ],
    )

    bi = BusinessIntelligence(
        business_meaning=raw_output.business_meaning,
        technical_summary=raw_output.technical_summary,
        affected_entities=raw_output.affected_entities,
        sap_module=raw_output.sap_module,
        query_type=raw_output.query_type,
        risk_summary=raw_output.risk_summary,
        final_learning=raw_output.final_learning,
        governance=raw_output.governance,
        meta=meta,
    )

    return bi, combined_report
