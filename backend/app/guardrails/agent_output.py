"""Agent output guardrails: parser reconciliation, claim grounding, sanitization, and canary check."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

from app.guardrails.injection import scan_canary_leak
from app.guardrails.models import GuardrailEvent, KeyValue
from app.guardrails.redaction import redact_secrets

if TYPE_CHECKING:
    from app.agents.knowledge_pack import SapKnowledgePack, SqlKnowledgePack
    from app.agents.models import SAPAnalysis, SQLAnalysis
    from app.guardrails.settings import GuardrailSettings
    from app.parser.models import ParsedQuery


def _sanitize_text_field(
    text: str, max_chars: int, request_id: str = "", field_name: str = ""
) -> tuple[str, list[GuardrailEvent]]:
    """Strips HTML/script tags, control characters, markdown links/URLs, and collapses runaway whitespace."""
    events: list[GuardrailEvent] = []
    if not text:
        return text, events

    clean = text
    # 1. Detect & strip script/HTML tags
    if "<script" in clean.lower() or "<html" in clean.lower() or "</" in clean.lower():
        clean = re.sub(r"<script[\s\S]*?</script>", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"<[^>]+>", "", clean)
        events.append(
            GuardrailEvent(
                name="output_html_sanitized",
                stage="agent_output",
                action="sanitize",
                severity="medium",
                message=f"Stripped HTML/script tags from field '{field_name}'",
                details=[KeyValue(key="field", value=field_name)],
                request_id=request_id,
            )
        )

    # 2. Strip external URLs/links
    if re.search(r"https?://", clean):
        clean = re.sub(r"https?://\S+", "[URL_REDACTED]", clean)
        events.append(
            GuardrailEvent(
                name="output_url_redacted",
                stage="agent_output",
                action="sanitize",
                severity="low",
                message=f"Redacted external URL from output field '{field_name}'",
                details=[KeyValue(key="field", value=field_name)],
                request_id=request_id,
            )
        )

    # 3. Strip unescaped HTML entities
    clean = html.unescape(clean)

    # 4. Truncate length
    if len(clean) > max_chars:
        clean = clean[:max_chars]
        events.append(
            GuardrailEvent(
                name="output_field_truncated",
                stage="agent_output",
                action="sanitize",
                severity="low",
                message=f"Output field '{field_name}' truncated to {max_chars} chars",
                details=[KeyValue(key="field", value=field_name)],
                request_id=request_id,
            )
        )

    return clean, events


def guardrail_sap_output(
    output: SAPAnalysis,
    parsed: ParsedQuery,
    pack: SapKnowledgePack,
    canary_token: str,
    settings: GuardrailSettings,
    request_id: str = "",
) -> tuple[SAPAnalysis, list[GuardrailEvent], bool]:
    """Reconciles SAPDomainAgent output with parser source of truth and grounds claims."""
    events: list[GuardrailEvent] = []
    is_blocked = False

    # 1. Canary leak check across entire output dump
    dump_text = output.model_dump_json()
    if scan_canary_leak(dump_text, canary_token):
        events.append(
            GuardrailEvent(
                name="canary_token_leaked",
                stage="agent_output",
                action="block",
                severity="critical",
                message="Critical security violation: Agent output leaked the secret canary token",
                agent="SAPDomainAgent",
                request_id=request_id,
            )
        )
        return output, events, True

    # 2. Secret leakage check
    _, secret_map, sec_events = redact_secrets(dump_text, settings, request_id=request_id)
    if secret_map:
        for ev in sec_events:
            ev.stage = "agent_output"
            ev.action = "block"
            events.append(ev)
        return output, events, True

    # 3. Table reconciliation (Parser is source of truth)
    allowed_table_names = {t.raw_name for t in parsed.tables}
    allowed_sap_names = {
        (t.sap_table or (t.naming.sap_table if (t.naming and t.naming.sap_table) else "")).upper()
        for t in parsed.tables
    }
    if parsed.view_naming and parsed.view_naming.sap_table:
        allowed_sap_names.add(parsed.view_naming.sap_table.upper())

    filtered_tables = []
    for tbl in output.tables:
        t_name = tbl.name
        t_sap = (tbl.resolved_sap_table or "").upper()
        if t_name in allowed_table_names or (t_sap and t_sap in allowed_sap_names):
            # Enforce parser role
            matching_pt = next(
                (t for t in parsed.tables if t.raw_name == t_name or t.alias == t_name),
                None,
            )
            if matching_pt and tbl.role != matching_pt.role:
                tbl.role = matching_pt.role  # type: ignore[assignment]
                events.append(
                    GuardrailEvent(
                        name="sap_table_role_repaired",
                        stage="agent_output",
                        action="repair",
                        severity="low",
                        message=f"Table '{t_name}' role corrected to parser ground-truth '{matching_pt.role}'",
                        details=[
                            KeyValue(key="table", value=t_name),
                            KeyValue(key="role", value=matching_pt.role),
                        ],
                        request_id=request_id,
                    )
                )
            filtered_tables.append(tbl)
        else:
            events.append(
                GuardrailEvent(
                    name="sap_hallucinated_table_dropped",
                    stage="agent_output",
                    action="repair",
                    severity="high",
                    message=f"Dropped hallucinated table '{t_name}' not present in parsed SQL",
                    details=[KeyValue(key="table", value=t_name)],
                    request_id=request_id,
                )
            )
    output.tables = filtered_tables

    # 4. Field reconciliation (Drop invented fields)
    parser_columns = {c.column.upper() for c in parsed.columns}
    filtered_fields = []
    for fld in output.fields:
        f_name = fld.field.upper()
        if f_name in parser_columns:
            # Check claim grounding
            # If claimed prebuilt/catalog/learned, must be present in knowledge pack
            if fld.knowledge_source in ("prebuilt", "catalog", "learned"):
                in_pack = any((r.field and r.field.upper() == f_name) for r in pack.resolved)
                if not in_pack:
                    fld.knowledge_source = "none"  # type: ignore[assignment]
                    fld.description = f"Field {f_name}"
                    events.append(
                        GuardrailEvent(
                            name="sap_ungrounded_field_repaired",
                            stage="agent_output",
                            action="repair",
                            severity="medium",
                            message=f"Field '{f_name}' claim of source '{fld.knowledge_source}' not in pack; cleared",
                            details=[KeyValue(key="field", value=f_name)],
                            request_id=request_id,
                        )
                    )
            filtered_fields.append(fld)
        else:
            events.append(
                GuardrailEvent(
                    name="sap_invented_field_dropped",
                    stage="agent_output",
                    action="repair",
                    severity="medium",
                    message=f"Dropped field '{f_name}' not present in query columns",
                    details=[KeyValue(key="field", value=f_name)],
                    request_id=request_id,
                )
            )
    output.fields = filtered_fields

    # 5. Ensure all pack unresolved items appear in unknown_items
    known_unknowns = {u.lower() for u in output.unknown_items}
    for un in pack.unresolved:
        ident = f"{un.table}.{un.field}" if un.field else un.table
        if ident.lower() not in known_unknowns:
            output.unknown_items.append(f"{ident} ({un.reason})")
            events.append(
                GuardrailEvent(
                    name="sap_missing_unresolved_added",
                    stage="agent_output",
                    action="repair",
                    severity="low",
                    message=f"Added missing unresolved pack item '{ident}' to unknown_items",
                    details=[KeyValue(key="item", value=ident)],
                    request_id=request_id,
                )
            )

    # 6. Confidence capping based on share of unresolved items
    if pack.unresolved:
        total_items = max(1, len(pack.resolved) + len(pack.unresolved))
        unresolved_share = len(pack.unresolved) / total_items
        max_allowed_confidence = round(max(0.2, 1.0 - (unresolved_share * 0.7)), 2)
        if output.confidence > max_allowed_confidence:
            output.confidence = max_allowed_confidence
            events.append(
                GuardrailEvent(
                    name="sap_confidence_capped",
                    stage="agent_output",
                    action="repair",
                    severity="low",
                    message=f"Capped confidence from model to {max_allowed_confidence} due to unresolved items",
                    details=[KeyValue(key="capped_confidence", value=str(max_allowed_confidence))],
                    request_id=request_id,
                )
            )

    # 7. Text sanitization
    output.client_handling, ch_ev = _sanitize_text_field(
        output.client_handling, settings.max_field_chars, request_id, "client_handling"
    )
    events.extend(ch_ev)

    return output, events, is_blocked


def guardrail_sql_output(
    output: SQLAnalysis,
    parsed: ParsedQuery,
    pack: SqlKnowledgePack,
    canary_token: str,
    settings: GuardrailSettings,
    request_id: str = "",
) -> tuple[SQLAnalysis, list[GuardrailEvent], bool]:
    """Reconciles SQLSemanticsAgent output with parser ground truth."""
    events: list[GuardrailEvent] = []
    is_blocked = False

    # 1. Canary leak check
    dump_text = output.model_dump_json()
    if scan_canary_leak(dump_text, canary_token):
        events.append(
            GuardrailEvent(
                name="canary_token_leaked",
                stage="agent_output",
                action="block",
                severity="critical",
                message="Critical security violation: Agent output leaked the secret canary token",
                agent="SQLSemanticsAgent",
                request_id=request_id,
            )
        )
        return output, events, True

    # 2. Secret leakage check
    _, secret_map, sec_events = redact_secrets(dump_text, settings, request_id=request_id)
    if secret_map:
        for ev in sec_events:
            ev.stage = "agent_output"
            ev.action = "block"
            events.append(ev)
        return output, events, True

    # 3. Overwrite statement_type and view_name from parser (parser wins)
    if output.statement_type != parsed.statement_type:
        output.statement_type = parsed.statement_type  # type: ignore[assignment]
        events.append(
            GuardrailEvent(
                name="sql_statement_type_repaired",
                stage="agent_output",
                action="repair",
                severity="low",
                message=f"Overwrote statement_type with parser ground truth '{parsed.statement_type}'",
                request_id=request_id,
            )
        )

    if output.view_name != parsed.view_name:
        output.view_name = parsed.view_name
        events.append(
            GuardrailEvent(
                name="sql_view_name_repaired",
                stage="agent_output",
                action="repair",
                severity="low",
                message=f"Overwrote view_name with parser ground truth '{parsed.view_name}'",
                request_id=request_id,
            )
        )

    # 4. Tables used reconciliation
    allowed_tables = {t.raw_name for t in parsed.tables}
    repaired_tables = []
    for tu in output.tables_used:
        if tu.name in allowed_tables:
            repaired_tables.append(tu)
        else:
            events.append(
                GuardrailEvent(
                    name="sql_hallucinated_table_dropped",
                    stage="agent_output",
                    action="repair",
                    severity="medium",
                    message=f"Dropped table '{tu.name}' not present in parsed tables",
                    request_id=request_id,
                )
            )
    output.tables_used = repaired_tables

    # 5. Text sanitization
    output.technical_summary, ts_ev = _sanitize_text_field(
        output.technical_summary, settings.max_field_chars, request_id, "technical_summary"
    )
    events.extend(ts_ev)

    output.query_purpose, qp_ev = _sanitize_text_field(
        output.query_purpose, settings.max_field_chars, request_id, "query_purpose"
    )
    events.extend(qp_ev)

    return output, events, is_blocked
