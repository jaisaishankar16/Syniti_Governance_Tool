"""Pre-flight and post-flight guardrail pipeline orchestration."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.guardrails.agent_output import guardrail_sap_output, guardrail_sql_output
from app.guardrails.injection import (
    generate_boundary_id,
    generate_canary_token,
    neutralize_delimiters,
    scan_prompt_injection,
)
from app.guardrails.input_checks import (
    check_input_limits,
    check_looks_like_sql,
    sanitize_input_characters,
)
from app.guardrails.models import GuardrailBlocked, GuardrailReport, SanitizedInput
from app.guardrails.redaction import check_and_handle_pii, redact_secrets
from app.guardrails.settings import GuardrailSettings, get_guardrail_settings

if TYPE_CHECKING:
    from app.agents.knowledge_pack import SapKnowledgePack, SqlKnowledgePack
    from app.agents.models import SAPAnalysis, SQLAnalysis
    from app.parser.models import ParsedQuery


def extract_raw_comments(sql: str) -> list[str]:
    """Extracts line and block comments from raw SQL as untrusted data."""
    raw_comments = re.findall(r"(?:--[^\r\n]*|/\*[\s\S]*?\*/)", sql)
    clean_comments: list[str] = []
    for c in raw_comments:
        c_clean = c.strip()
        if c_clean.startswith("--"):
            c_clean = c_clean[2:].strip()
        elif c_clean.startswith("/*") and c_clean.endswith("*/"):
            c_clean = c_clean[2:-2].strip()
        if c_clean:
            clean_comments.append(c_clean)
    return clean_comments


def pre_flight_pipeline(
    raw_sql: str,
    settings: GuardrailSettings | None = None,
    request_id: str = "",
) -> SanitizedInput:
    """Executes pre-flight guardrail checks on input SQL.

    Steps:
      1. Charset / control char / bidi override stripping
      2. Size, nesting, token length, and statement limits
      3. 'Looks like SQL' structural validation
      4. Credential & secret redaction (stable placeholders)
      5. PII detection / redaction / blocking
      6. Prompt injection scanning (in comments, literals, identifiers)
      7. Boundary ID & Canary Token generation
      8. Untrusted comment extraction and delimiter neutralization

    Raises:
        GuardrailBlocked: if any check fails with action='block'
    """
    cfg = settings or get_guardrail_settings()
    report = GuardrailReport()

    # 1. Charset / invisible / bidi character sanitization
    sanitized_chars_sql, char_events = sanitize_input_characters(
        raw_sql, cfg, request_id=request_id
    )
    for ev in char_events:
        report.add_event(ev)

    # 2. Input limits check
    limit_events = check_input_limits(sanitized_chars_sql, cfg, request_id=request_id)
    for ev in limit_events:
        report.add_event(ev)

    # 3. Looks like SQL check
    sql_structure_events = check_looks_like_sql(sanitized_chars_sql, cfg, request_id=request_id)
    for ev in sql_structure_events:
        report.add_event(ev)

    # Fast-fail if limits or non-SQL blocked before deep inspection
    if report.blocked:
        raise GuardrailBlocked(
            "Pre-flight guardrail blocked input due to structure or limit violations",
            report,
        )

    # 4. Secret redaction
    redacted_sql, secret_map, sec_events = redact_secrets(
        sanitized_chars_sql, cfg, request_id=request_id
    )
    for ev in sec_events:
        report.add_event(ev)

    # 5. PII check and handling
    pii_handled_sql, pii_events = check_and_handle_pii(redacted_sql, cfg, request_id=request_id)
    for ev in pii_events:
        report.add_event(ev)

    # 6. Prompt injection scanning
    _, inj_events, is_suspected = scan_prompt_injection(pii_handled_sql, cfg, request_id=request_id)
    for ev in inj_events:
        report.add_event(ev)

    # Fast-fail if secret/PII/injection triggered a block
    if report.blocked:
        raise GuardrailBlocked(
            "Pre-flight guardrail blocked input due to security policy violations",
            report,
        )

    # 7. Boundary ID & Canary generation
    boundary_id = generate_boundary_id()
    canary_token = generate_canary_token(boundary_id)

    # 8. Untrusted comment extraction & delimiter escaping
    raw_comments = extract_raw_comments(raw_sql)
    neutralized_comments = [neutralize_delimiters(c, boundary_id) for c in raw_comments]

    return SanitizedInput(
        original_sql=raw_sql,
        sanitized_sql=pii_handled_sql,
        untrusted_comments=neutralized_comments,
        boundary_id=boundary_id,
        canary_token=canary_token,
        secret_map=secret_map,
        injection_suspected=is_suspected,
        report=report,
    )


def post_flight_pipeline(
    sap_output: SAPAnalysis,
    sql_output: SQLAnalysis,
    parsed: ParsedQuery,
    sap_pack: SapKnowledgePack,
    sql_pack: SqlKnowledgePack,
    canary_token: str,
    settings: GuardrailSettings | None = None,
    request_id: str = "",
) -> tuple[SAPAnalysis, SQLAnalysis, GuardrailReport]:
    """Applies agent output guardrails, parser ground-truth reconciliation, and canary checks.

    Returns:
        (reconciled_sap, reconciled_sql, combined_report)
    """
    cfg = settings or get_guardrail_settings()
    report = GuardrailReport()

    # 1. SAP agent output guardrails
    rec_sap, sap_events, sap_blocked = guardrail_sap_output(
        sap_output, parsed, sap_pack, canary_token, cfg, request_id=request_id
    )
    for ev in sap_events:
        report.add_event(ev)

    # 2. SQL agent output guardrails
    rec_sql, sql_events, sql_blocked = guardrail_sql_output(
        sql_output, parsed, sql_pack, canary_token, cfg, request_id=request_id
    )
    for ev in sql_events:
        report.add_event(ev)

    if sap_blocked or sql_blocked:
        report.blocked = True

    return rec_sap, rec_sql, report
