"""Logging filters and structlog processors for redacting secrets and sensitive SQL text."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from app.guardrails.settings import get_guardrail_settings

# Fast regex for common secrets in log messages
SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"password\s*=\s*['\"][^'\"]+['\"]", re.IGNORECASE),
]

SQL_KEYWORDS_REGEX = re.compile(
    r"\b(SELECT\s+[\s\S]+?\s+FROM|CREATE\s+VIEW|INSERT\s+INTO|UPDATE\s+\w+\s+SET)\b",
    re.IGNORECASE,
)


def mask_secrets_in_text(text: str) -> str:
    """Replaces detected secrets in arbitrary log text with a redaction token."""
    redacted = text
    for pat in SECRET_PATTERNS:
        redacted = pat.sub("[REDACTED_SECRET]", redacted)
    return redacted


def mask_sql_in_text(text: str, debug_sql: bool = False) -> str:
    """Summarizes SQL statements to sha256 and length unless debug_sql is True."""
    if debug_sql or not text:
        return text

    def _replace_sql(match: re.Match[str]) -> str:
        sql_body = match.group(0)
        h = hashlib.sha256(sql_body.encode("utf-8")).hexdigest()[:12]
        return f"[SQL len={len(sql_body)} sha256={h}]"

    return SQL_KEYWORDS_REGEX.sub(_replace_sql, text)


class GuardrailLoggingFilter(logging.Filter):
    """Standard library logging filter redacting credentials and raw SQL."""

    def __init__(self, name: str = "", debug_log_sql: bool | None = None) -> None:
        super().__init__(name)
        cfg = get_guardrail_settings()
        self.debug_log_sql = debug_log_sql if debug_log_sql is not None else cfg.debug_log_sql

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            masked = mask_secrets_in_text(record.msg)
            masked = mask_sql_in_text(masked, self.debug_log_sql)
            record.msg = masked

        if record.args:
            cleaned_args = []
            for arg in record.args if isinstance(record.args, tuple) else [record.args]:
                if isinstance(arg, str):
                    arg = mask_secrets_in_text(arg)
                    arg = mask_sql_in_text(arg, self.debug_log_sql)
                cleaned_args.append(arg)
            record.args = tuple(cleaned_args)

        return True


def structlog_guardrail_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor redacting credentials and raw SQL queries from event dicts."""
    cfg = get_guardrail_settings()
    debug_sql = cfg.debug_log_sql

    for key, val in list(event_dict.items()):
        if isinstance(val, str):
            val = mask_secrets_in_text(val)
            if key.lower() in ("sql", "query", "raw_sql", "normalized_sql") and not debug_sql:
                h = hashlib.sha256(val.encode("utf-8")).hexdigest()[:12]
                val = f"[SQL len={len(val)} sha256={h}]"
            else:
                val = mask_sql_in_text(val, debug_sql)
            event_dict[key] = val

    return event_dict
