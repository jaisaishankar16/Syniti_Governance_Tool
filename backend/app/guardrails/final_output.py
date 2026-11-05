"""Final output guardrail validation: SQL safety & subset, glossary validation, literal grounding, risk floor, and text sanitization."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.guardrails.injection import scan_canary_leak
from app.guardrails.models import (
    GuardrailEvent,
    GuardrailReport,
    KeyValue,
)
from app.guardrails.redaction import redact_secrets
from app.guardrails.settings import GuardrailSettings

if TYPE_CHECKING:
    from app.knowledge.glossary import GlossaryHelper


def sanitize_text(
    text: str, max_chars: int = 5000, field_name: str = ""
) -> tuple[str, list[GuardrailEvent]]:
    """Sanitizes text fields: strips HTML/script tags, external URLs, and truncates length."""
    events: list[GuardrailEvent] = []
    if not text:
        return text, events

    clean = text
    # Strip script/html tags
    if "<script" in clean.lower() or "<html" in clean.lower() or "</" in clean.lower():
        clean = re.sub(r"<script[\s\S]*?</script>", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"<[^>]+>", "", clean)
        events.append(
            GuardrailEvent(
                name="final_output_html_sanitized",
                stage="final_output",
                action="sanitize",
                severity="medium",
                message=f"Stripped HTML/script tags from field '{field_name}'",
                details=[KeyValue(key="field", value=field_name)],
            )
        )

    # Redact URLs
    if re.search(r"https?://", clean):
        clean = re.sub(r"https?://\S+", "[URL_REDACTED]", clean)
        events.append(
            GuardrailEvent(
                name="final_output_url_redacted",
                stage="final_output",
                action="sanitize",
                severity="low",
                message=f"Redacted external URL from field '{field_name}'",
                details=[KeyValue(key="field", value=field_name)],
            )
        )

    if len(clean) > max_chars:
        clean = clean[:max_chars]
        events.append(
            GuardrailEvent(
                name="final_output_field_truncated",
                stage="final_output",
                action="sanitize",
                severity="low",
                message=f"Field '{field_name}' truncated to {max_chars} characters",
                details=[KeyValue(key="field", value=field_name)],
            )
        )

    return clean, events


def validate_canary_and_secrets(
    text_content: str, canary_token: str, settings: GuardrailSettings, request_id: str = ""
) -> GuardrailReport:
    """Verifies that final output does not leak canary tokens or credentials."""
    report = GuardrailReport()

    if canary_token and scan_canary_leak(text_content, canary_token):
        report.add_event(
            GuardrailEvent(
                name="canary_token_leaked_final_output",
                stage="final_output",
                action="block",
                severity="critical",
                message="Critical security violation: Canary token leaked in final synthesized output",
                request_id=request_id,
            )
        )
        return report

    _, secret_map, sec_events = redact_secrets(text_content, settings, request_id=request_id)
    if secret_map:
        for ev in sec_events:
            ev.stage = "final_output"
            ev.action = "block"
            report.add_event(ev)

    return report


def validate_literal_grounding(
    sql_text: str,
    known_tokens: set[str],
    request_id: str = "",
) -> GuardrailReport:
    """Detects unexplained magic literals in SQL predicates and warns if not grounded."""
    report = GuardrailReport()
    # Find string and numeric literals in WHERE / HAVING expressions
    literals = re.findall(r"(?:'([^']*)'|\b(\d+)\b)", sql_text)
    for str_val, num_val in literals:
        lit = (str_val or num_val).strip()
        # Skip trivial literals 0, 1, empty
        if not lit or lit in ("0", "1", "''", " ", "TRUE", "FALSE"):
            continue
        if lit.upper() not in known_tokens:
            report.add_event(
                GuardrailEvent(
                    name="ungrounded_literal_warning",
                    stage="final_output",
                    action="warn",
                    severity="low",
                    message=f"Literal constant '{lit}' in generated SQL has no matching token in comments or catalog",
                    details=[KeyValue(key="literal", value=lit)],
                    request_id=request_id,
                )
            )

    return report


def validate_risk_consistency_floor(
    synthesizer_risk_score: float,
    parser_risk_score: float,
    request_id: str = "",
) -> tuple[float, GuardrailReport]:
    """Ensures synthesized risk score does not drop below parser deterministic risk floor."""
    report = GuardrailReport()
    if synthesizer_risk_score < parser_risk_score:
        report.add_event(
            GuardrailEvent(
                name="risk_score_floor_enforced",
                stage="final_output",
                action="repair",
                severity="medium",
                message=f"Elevated synthesized risk score from {synthesizer_risk_score:.2f} to parser risk floor {parser_risk_score:.2f}",
                details=[
                    KeyValue(key="original_synthesizer_score", value=str(synthesizer_risk_score)),
                    KeyValue(key="parser_floor", value=str(parser_risk_score)),
                ],
                request_id=request_id,
            )
        )
        return parser_risk_score, report

    return synthesizer_risk_score, report


def validate_glossary_membership(
    concept_group: str,
    term: str,
    glossary_helper: GlossaryHelper | None = None,
    request_id: str = "",
) -> GuardrailReport:
    """Checks if a term belongs to the official governance glossary."""
    report = GuardrailReport()
    if not term or not glossary_helper:
        return report

    options = glossary_helper.list_options(concept_group)
    if not options:
        return report

    normalized_options = {opt.lower() for opt in options}
    if term.lower() not in normalized_options:
        report.add_event(
            GuardrailEvent(
                name="glossary_term_unrecognized",
                stage="final_output",
                action="warn",
                severity="low",
                message=f"Term '{term}' not found in official glossary group '{concept_group}'",
                details=[
                    KeyValue(key="concept_group", value=concept_group),
                    KeyValue(key="term", value=term),
                ],
                request_id=request_id,
            )
        )

    return report
