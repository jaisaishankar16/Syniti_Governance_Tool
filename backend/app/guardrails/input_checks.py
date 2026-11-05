"""Input guardrail checks: size, character encoding, invisible/bidi characters, and SQL structure."""

from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING, Literal

from app.guardrails.models import GuardrailEvent, KeyValue

if TYPE_CHECKING:
    from app.guardrails.settings import GuardrailSettings

# Invisible, zero-width, and Trojan Source bidirectional override characters
ZERO_WIDTH_CHARS = {
    "\u200b",  # Zero-width space
    "\u200c",  # Zero-width non-joiner
    "\u200d",  # Zero-width joiner
    "\ufeff",  # Zero-width no-break space / BOM
    "\u2060",  # Word joiner
    "\u00ad",  # Soft hyphen
}

BIDI_OVERRIDE_CHARS = {
    "\u202a",  # Left-to-Right Embedding
    "\u202b",  # Right-to-Left Embedding
    "\u202c",  # Pop Directional Formatting
    "\u202d",  # Left-to-Right Override
    "\u202e",  # Right-to-Left Override
    "\u2066",  # Left-to-Right Isolate
    "\u2067",  # Right-to-Left Isolate
    "\u2068",  # First Strong Isolate
    "\u2069",  # Pop Directional Isolate
}

SQL_KEYWORDS_SET = {
    "SELECT",
    "WITH",
    "CREATE",
    "ALTER",
    "DROP",
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "EXEC",
    "EXECUTE",
    "TRUNCATE",
    "FROM",
    "WHERE",
    "JOIN",
    "UNION",
}


def sanitize_input_characters(
    raw_sql: str, settings: GuardrailSettings, request_id: str = ""
) -> tuple[str, list[GuardrailEvent]]:
    """Strips control chars (except \\n, \\r, \\t), zero-width chars, and bidi overrides."""
    events: list[GuardrailEvent] = []
    if settings.guard_mode_input_size == "off":
        return raw_sql, events

    clean_chars: list[str] = []
    stripped_control = 0
    stripped_zero_width = 0
    stripped_bidi = 0

    for ch in raw_sql:
        if ch in ZERO_WIDTH_CHARS:
            stripped_zero_width += 1
            continue
        if ch in BIDI_OVERRIDE_CHARS:
            stripped_bidi += 1
            continue

        # Keep valid whitespace: newline, carriage return, tab
        if ch in ("\n", "\r", "\t"):
            clean_chars.append(ch)
            continue

        # Strip other control characters (Unicode category 'Cc' or ordinal < 32 or 127)
        ord_val = ord(ch)
        if ord_val < 32 or ord_val == 127 or unicodedata.category(ch) == "Cc":
            stripped_control += 1
            continue

        clean_chars.append(ch)

    total_stripped = stripped_control + stripped_zero_width + stripped_bidi
    sanitized_sql = "".join(clean_chars)

    if total_stripped > 0:
        events.append(
            GuardrailEvent(
                name="input_charset_sanitization",
                stage="input",
                action="sanitize",
                severity="medium" if (stripped_bidi or stripped_zero_width) else "low",
                message=f"Stripped {total_stripped} control, zero-width, or bidi override characters from input SQL",
                details=[
                    KeyValue(key="stripped_control", value=str(stripped_control)),
                    KeyValue(key="stripped_zero_width", value=str(stripped_zero_width)),
                    KeyValue(key="stripped_bidi", value=str(stripped_bidi)),
                ],
                request_id=request_id,
            )
        )

    return sanitized_sql, events


def check_input_limits(
    sql: str, settings: GuardrailSettings, request_id: str = ""
) -> list[GuardrailEvent]:
    """Validates input size, statement count, nesting depth, and token length."""
    events: list[GuardrailEvent] = []
    if settings.guard_mode_input_size == "off":
        return events

    # 1. Total character count
    char_len = len(sql)
    if char_len > settings.max_sql_chars:
        action: Literal["pass", "warn", "sanitize", "repair", "block"] = (
            "block" if settings.guard_mode_input_size == "enforce" else "warn"
        )
        events.append(
            GuardrailEvent(
                name="input_max_chars_exceeded",
                stage="input",
                action=action,
                severity="high",
                message=f"Input SQL length ({char_len} chars) exceeds maximum allowed ({settings.max_sql_chars})",
                details=[KeyValue(key="length", value=str(char_len))],
                request_id=request_id,
            )
        )

    # 2. Maximum token length
    tokens = re.split(r"[\s,;()=<>+*\/\-]+", sql)
    max_tok = max((len(t) for t in tokens if t), default=0)
    if max_tok > settings.max_token_len:
        action = "block" if settings.guard_mode_input_size == "enforce" else "warn"
        events.append(
            GuardrailEvent(
                name="input_token_length_exceeded",
                stage="input",
                action=action,
                severity="high",
                message=f"A single token length ({max_tok} chars) exceeds maximum limit ({settings.max_token_len})",
                details=[KeyValue(key="max_token_length", value=str(max_tok))],
                request_id=request_id,
            )
        )

    # 3. Maximum statement count (rough check before parser)
    # Split on semicolons outside of quotes
    stmt_candidates = [s.strip() for s in sql.split(";") if s.strip()]
    if len(stmt_candidates) > settings.max_statements:
        action = "block" if settings.guard_mode_input_size == "enforce" else "warn"
        events.append(
            GuardrailEvent(
                name="input_max_statements_exceeded",
                stage="input",
                action=action,
                severity="medium",
                message=f"Input contains {len(stmt_candidates)} statements, exceeding maximum ({settings.max_statements})",
                details=[KeyValue(key="statement_count", value=str(len(stmt_candidates)))],
                request_id=request_id,
            )
        )

    # 4. Maximum nesting depth (parentheses check)
    max_depth = 0
    current_depth = 0
    for ch in sql:
        if ch == "(":
            current_depth += 1
            max_depth = max(max_depth, current_depth)
        elif ch == ")":
            if current_depth > 0:
                current_depth -= 1

    if max_depth > settings.max_nesting:
        action = "block" if settings.guard_mode_input_size == "enforce" else "warn"
        events.append(
            GuardrailEvent(
                name="input_max_nesting_exceeded",
                stage="input",
                action=action,
                severity="high",
                message=f"Input SQL parenthesis nesting depth ({max_depth}) exceeds maximum limit ({settings.max_nesting})",
                details=[KeyValue(key="nesting_depth", value=str(max_depth))],
                request_id=request_id,
            )
        )

    return events


def check_looks_like_sql(
    sql: str, settings: GuardrailSettings, request_id: str = ""
) -> list[GuardrailEvent]:
    """Rejects empty strings or plain prose/code lacking SQL keyword structure."""
    events: list[GuardrailEvent] = []
    if settings.guard_mode_looks_like_sql == "off":
        return events

    stripped = sql.strip()
    if not stripped:
        action: Literal["pass", "warn", "sanitize", "repair", "block"] = (
            "block" if settings.guard_mode_looks_like_sql == "enforce" else "warn"
        )
        events.append(
            GuardrailEvent(
                name="input_empty_sql",
                stage="input",
                action=action,
                severity="high",
                message="Input SQL query is empty",
                request_id=request_id,
            )
        )
        return events

    # Extract uppercase alphanumeric words
    words = set(re.findall(r"\b[A-Za-z_]+\b", stripped.upper()))
    matching_keywords = words.intersection(SQL_KEYWORDS_SET)

    if not matching_keywords:
        action = "block" if settings.guard_mode_looks_like_sql == "enforce" else "warn"
        events.append(
            GuardrailEvent(
                name="input_not_sql",
                stage="input",
                action=action,
                severity="high",
                message="Input text does not contain recognizable SQL statements or keywords (rejected non-SQL payload)",
                details=[KeyValue(key="sample_text", value=stripped[:100])],
                request_id=request_id,
            )
        )

    return events
