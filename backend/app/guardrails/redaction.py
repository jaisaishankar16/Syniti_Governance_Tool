"""Secret redaction and PII detection guardrails."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.guardrails.models import GuardrailEvent, KeyValue

if TYPE_CHECKING:
    from app.guardrails.settings import GuardrailSettings


def _luhn_check(card_number_str: str) -> bool:
    """Validates credit card checksum using the Luhn formula."""
    digits = [int(c) for c in card_number_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, digit in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = digit * 2
            checksum += (doubled - 9) if doubled > 9 else doubled
        else:
            checksum += digit
    return checksum % 10 == 0


def _load_pattern_file(path: Path) -> list[dict[str, Any]]:
    """Loads JSON pattern definition list from file."""
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("patterns", [])
    except (json.JSONDecodeError, OSError):
        return []


def redact_secrets(
    sql: str, settings: GuardrailSettings, request_id: str = ""
) -> tuple[str, dict[str, str], list[GuardrailEvent]]:
    """Detects credentials, keys, passwords, and tokens, redacting them to placeholders."""
    events: list[GuardrailEvent] = []
    secret_map: dict[str, str] = {}
    if settings.guard_mode_secrets == "off":
        return sql, secret_map, events

    patterns = _load_pattern_file(settings.secret_patterns_path)
    redacted_sql = sql
    counter = 1

    for pat in patterns:
        pat_id = pat.get("id", "SEC")
        name = pat.get("name", "Secret")
        regex_str = pat.get("regex", "")
        severity = pat.get("severity", "critical")

        try:
            rx = re.compile(regex_str)
        except re.error:
            continue

        for m in list(rx.finditer(redacted_sql)):
            matched_text = m.group(0)
            # If pattern has a capturing group (e.g. password=value), redact the group value
            secret_val = m.group(1) if m.lastindex and m.lastindex >= 1 else matched_text
            if not secret_val or secret_val in secret_map.values():
                continue

            placeholder = f"<REDACTED_SECRET_{counter}>"
            counter += 1
            secret_map[placeholder] = secret_val

            redacted_sql = redacted_sql.replace(secret_val, placeholder)

            events.append(
                GuardrailEvent(
                    name=f"secret_detected_{pat_id}",
                    stage="input",
                    action="sanitize",
                    severity=severity,  # type: ignore[arg-type]
                    message=f"Detected and redacted sensitive credential ({name}) to {placeholder}",
                    details=[
                        KeyValue(key="pattern_id", value=pat_id),
                        KeyValue(key="placeholder", value=placeholder),
                    ],
                    request_id=request_id,
                )
            )

    return redacted_sql, secret_map, events


def check_and_handle_pii(
    sql: str, settings: GuardrailSettings, request_id: str = ""
) -> tuple[str, list[GuardrailEvent]]:
    """Detects Personally Identifiable Information (emails, phones, SSN, IBAN, credit cards)."""
    events: list[GuardrailEvent] = []
    mode = settings.guard_mode_pii
    if mode == "off":
        return sql, events

    patterns = _load_pattern_file(settings.pii_patterns_path)
    current_sql = sql
    counter = 1

    for pat in patterns:
        pat_id = pat.get("id", "PII")
        name = pat.get("name", "PII")
        regex_str = pat.get("regex", "")
        validator = pat.get("validator")
        severity = pat.get("severity", "low")

        try:
            rx = re.compile(regex_str)
        except re.error:
            continue

        for m in list(rx.finditer(current_sql)):
            val = m.group(0)
            if validator == "luhn" and not _luhn_check(val):
                continue

            if mode == "warn":
                events.append(
                    GuardrailEvent(
                        name=f"pii_detected_{pat_id}",
                        stage="input",
                        action="warn",
                        severity=severity,  # type: ignore[arg-type]
                        message=f"Potentially sensitive PII detected in query ({name})",
                        details=[KeyValue(key="pattern_id", value=pat_id)],
                        request_id=request_id,
                    )
                )
            elif mode == "redact":
                placeholder = f"<REDACTED_PII_{counter}>"
                counter += 1
                current_sql = current_sql.replace(val, placeholder)
                events.append(
                    GuardrailEvent(
                        name=f"pii_redacted_{pat_id}",
                        stage="input",
                        action="sanitize",
                        severity=severity,  # type: ignore[arg-type]
                        message=f"Redacted sensitive PII ({name}) to {placeholder}",
                        details=[
                            KeyValue(key="pattern_id", value=pat_id),
                            KeyValue(key="placeholder", value=placeholder),
                        ],
                        request_id=request_id,
                    )
                )
            elif mode == "block":
                events.append(
                    GuardrailEvent(
                        name=f"pii_blocked_{pat_id}",
                        stage="input",
                        action="block",
                        severity="high",
                        message=f"Blocked query containing unredacted PII ({name})",
                        details=[KeyValue(key="pattern_id", value=pat_id)],
                        request_id=request_id,
                    )
                )

    return current_sql, events
