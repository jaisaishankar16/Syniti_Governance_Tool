"""Tool execution policies, argument validation, learned-knowledge hardening, and write caps."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.guardrails.injection import scan_prompt_injection
from app.guardrails.models import GuardrailEvent, KeyValue

if TYPE_CHECKING:
    from app.agents.models import AgentDeps
    from app.guardrails.settings import GuardrailSettings

logger = logging.getLogger(__name__)

SAFE_IDENTIFIER_REGEX = re.compile(r"^[A-Za-z0-9_$#]{1,128}$")

ALLOWED_AGENT_TOOLS: dict[str, set[str]] = {
    "SQLSemanticsAgent": {"lookup_sql_keyword", "search_sql_docs"},
    "SAPDomainAgent": {
        "lookup_sap_knowledge",
        "search_sap_catalog",
        "save_learned_sap_knowledge",
    },
}

APPROVALS_FILE_NAME = "approved_learned_keys.txt"


def validate_tool_call_permission(
    agent_name: str, tool_name: str, settings: GuardrailSettings, request_id: str = ""
) -> GuardrailEvent | None:
    """Verifies that the requested tool is on the agent's explicit allowlist."""
    allowed = ALLOWED_AGENT_TOOLS.get(agent_name, set())
    if tool_name not in allowed:
        return GuardrailEvent(
            name="tool_permission_denied",
            stage="tool",
            action="block",
            severity="critical",
            message=f"Agent '{agent_name}' attempted to invoke unpermitted tool '{tool_name}'",
            details=[
                KeyValue(key="agent", value=agent_name),
                KeyValue(key="tool", value=tool_name),
            ],
            agent=agent_name,
            request_id=request_id,
        )
    return None


def validate_tool_arguments(
    tool_name: str,
    kwargs: dict[str, Any],
    settings: GuardrailSettings,
    request_id: str = "",
) -> list[GuardrailEvent]:
    """Validates tool argument formats, lengths, and scans for injection payloads."""
    events: list[GuardrailEvent] = []

    # 1. Identifier checks
    for arg_name in ("table_name", "field_name", "term"):
        if kwargs.get(arg_name):
            val = str(kwargs[arg_name])
            if not SAFE_IDENTIFIER_REGEX.match(val):
                events.append(
                    GuardrailEvent(
                        name="tool_invalid_identifier",
                        stage="tool",
                        action="block",
                        severity="high",
                        message=f"Argument '{arg_name}' ('{val[:30]}') failed alphanumeric identifier validation",
                        details=[
                            KeyValue(key="arg", value=arg_name),
                            KeyValue(key="val", value=val[:50]),
                        ],
                        request_id=request_id,
                    )
                )

    # 2. Search query constraints
    for q_arg in ("query", "search_text"):
        if kwargs.get(q_arg):
            query_str = str(kwargs[q_arg])
            if len(query_str) > settings.max_tool_query_chars:
                events.append(
                    GuardrailEvent(
                        name="tool_query_length_exceeded",
                        stage="tool",
                        action="block",
                        severity="medium",
                        message=f"Search query length ({len(query_str)}) exceeds maximum allowed ({settings.max_tool_query_chars})",
                        details=[KeyValue(key="length", value=str(len(query_str)))],
                        request_id=request_id,
                    )
                )

            # Check injection in query arguments
            _, inj_events, suspected = scan_prompt_injection(
                query_str, settings, request_id=request_id
            )
            if suspected:
                for ev in inj_events:
                    ev.stage = "tool"
                    events.append(ev)

    # 3. k limit
    if "k" in kwargs and kwargs["k"] is not None:
        try:
            k_val = int(kwargs["k"])
            if k_val > 5:
                events.append(
                    GuardrailEvent(
                        name="tool_k_exceeded",
                        stage="tool",
                        action="sanitize",
                        severity="low",
                        message=f"Requested top_k={k_val} exceeds limit of 5; capped to 5",
                        details=[KeyValue(key="requested_k", value=str(k_val))],
                        request_id=request_id,
                    )
                )
        except (ValueError, TypeError):
            pass

    return events


def check_learned_write_allowed(
    deps: AgentDeps,
    field_or_table: str,
    description: str,
    evidence_text: str,
    settings: GuardrailSettings,
) -> tuple[bool, list[GuardrailEvent]]:
    """Enforces write-cap, prior-search requirement, grounding, and kill-switch on save_learned_sap_knowledge."""
    events: list[GuardrailEvent] = []
    if settings.guard_mode_tool_policy == "off":
        return True, events

    # 1. Kill switch
    if not settings.learned_requires_approval and not deps.settings.learning_enabled:
        events.append(
            GuardrailEvent(
                name="learned_killswitch_active",
                stage="tool",
                action="block",
                severity="medium",
                message="Learning write refused: LEARNING_ENABLED is disabled",
                request_id=deps.request_id,
            )
        )
        return False, events

    # 2. Write cap
    write_count = getattr(deps.run_trace, "write_count", 0)
    if write_count >= settings.max_writes_per_run:
        events.append(
            GuardrailEvent(
                name="learned_write_cap_exceeded",
                stage="tool",
                action="block",
                severity="medium",
                message=f"Write cap reached ({settings.max_writes_per_run} writes per run)",
                details=[KeyValue(key="writes", value=str(write_count))],
                request_id=deps.request_id,
            )
        )
        return False, events

    # 3. Prior search requirement
    if deps.run_trace.search_count == 0 and not deps.run_trace.retrieved_chunks:
        events.append(
            GuardrailEvent(
                name="learned_missing_prior_search",
                stage="tool",
                action="block",
                severity="high",
                message="save_learned_sap_knowledge refused: must be preceded by a catalog search in this run",
                request_id=deps.request_id,
            )
        )
        return False, events

    # 4. Lexical grounding in evidence text
    # Extract lowercase words
    ev_words = set(re.findall(r"\b[a-z]{3,}\b", evidence_text.lower()))
    desc_words = set(re.findall(r"\b[a-z]{3,}\b", description.lower()))
    overlap = len(desc_words.intersection(ev_words))

    # Must share at least 1 content word or 15% overlap
    min_overlap = max(1, int(len(desc_words) * 0.15))
    if desc_words and overlap < min_overlap:
        events.append(
            GuardrailEvent(
                name="learned_ungrounded_description",
                stage="tool",
                action="block",
                severity="high",
                message="save_learned_sap_knowledge refused: description is not lexically grounded in evidence text",
                details=[
                    KeyValue(key="overlap_words", value=str(overlap)),
                    KeyValue(key="required_overlap", value=str(min_overlap)),
                ],
                request_id=deps.request_id,
            )
        )
        return False, events

    # 5. Length ratio check
    if len(description) > len(evidence_text) * 2.5:
        events.append(
            GuardrailEvent(
                name="learned_hallucinated_expansion",
                stage="tool",
                action="block",
                severity="high",
                message="save_learned_sap_knowledge refused: description is excessively longer than evidence",
                details=[
                    KeyValue(key="desc_len", value=str(len(description))),
                    KeyValue(key="evidence_len", value=str(len(evidence_text))),
                ],
                request_id=deps.request_id,
            )
        )
        return False, events

    return True, events


def is_learned_entry_approved(entry_key: str, repo_root: Path) -> bool:
    """Checks if a learned knowledge entry key has been approved in approvals file."""
    approvals_path = repo_root / "knowledge" / "learned" / APPROVALS_FILE_NAME
    if not approvals_path.is_file():
        return False
    try:
        content = approvals_path.read_text(encoding="utf-8")
        approved_keys = {
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.startswith("#")
        }
        return entry_key in approved_keys
    except OSError:
        return False
