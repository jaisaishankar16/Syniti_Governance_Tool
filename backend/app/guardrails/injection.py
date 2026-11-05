"""Prompt injection scanning, comment quarantine, delimiter hardening, and canary tokens."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.guardrails.models import GuardrailEvent, KeyValue

if TYPE_CHECKING:
    from app.guardrails.settings import GuardrailSettings


def _load_injection_patterns(path: Path) -> list[dict[str, Any]]:
    """Loads injection regex patterns from JSON."""
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("patterns", [])
    except (json.JSONDecodeError, OSError):
        return []


def generate_boundary_id() -> str:
    """Generates a random 8-character hex boundary ID for delimiter hardening."""
    return secrets.token_hex(4)


def generate_canary_token(boundary_id: str) -> str:
    """Generates a unique per-request canary token to detect system prompt exfiltration."""
    random_suffix = secrets.token_hex(4)
    return f"CANARY_{boundary_id}_{random_suffix}"


def scan_prompt_injection(
    sql: str, settings: GuardrailSettings, request_id: str = ""
) -> tuple[float, list[GuardrailEvent], bool]:
    """Scans SQL comments, string literals, and identifiers for prompt injection patterns.

    Returns:
        (total_score, events, is_suspected)
    """
    events: list[GuardrailEvent] = []
    if settings.guard_mode_injection == "off":
        return 0.0, events, False

    patterns = _load_injection_patterns(settings.injection_patterns_path)
    total_score = 0.0
    is_suspected = False

    # Extract comments
    comment_matches = re.findall(r"(?:--[^\r\n]*|/\*[\s\S]*?\*/)", sql)
    # Extract string literals
    literal_matches = re.findall(r"'(?:''|[^'])*'", sql)
    # Extract bracketed identifiers
    bracket_matches = re.findall(r"\[[^\]]+\]", sql)

    # Check each section
    scannable_parts = [
        ("comment", " ".join(comment_matches)),
        ("literal", " ".join(literal_matches)),
        ("identifier", " ".join(bracket_matches)),
        ("raw_sql", sql),
    ]

    for pat in patterns:
        pat_id = pat.get("id", "INJ")
        category = pat.get("category", "injection")
        regex_str = pat.get("regex", "")
        weight = float(pat.get("weight", 0.5))
        severity = pat.get("severity", "medium")

        try:
            rx = re.compile(regex_str, re.IGNORECASE)
        except re.error:
            continue

        for location, text in scannable_parts:
            if not text:
                continue
            matches = list(rx.finditer(text))
            if not matches:
                continue

            total_score += weight * len(matches)
            is_suspected = True

            action = "warn"
            if settings.guard_mode_injection == "block" and severity in ("high", "critical"):
                # Never block on a string literal alone unless critical
                if location != "literal" or severity == "critical":
                    action = "block"
            elif settings.guard_mode_injection == "sanitize":
                action = "sanitize" if location == "comment" else "warn"

            events.append(
                GuardrailEvent(
                    name=f"prompt_injection_{pat_id}",
                    stage="input",
                    action=action,  # type: ignore[arg-type]
                    severity=severity,  # type: ignore[arg-type]
                    message=f"Prompt injection pattern detected in SQL {location} ({category})",
                    details=[
                        KeyValue(key="category", value=category),
                        KeyValue(key="location", value=location),
                        KeyValue(key="pattern_id", value=pat_id),
                        KeyValue(key="matched_sample", value=matches[0].group(0)[:60]),
                    ],
                    request_id=request_id,
                )
            )
            break

    return total_score, events, is_suspected


def neutralize_delimiters(content: str, boundary_id: str) -> str:
    """Escapes section tags and delimiter closing patterns to prevent agent prompt breakout."""
    # Prevent fake boundary closing tags
    safe = re.sub(
        r"</?(task|query_facts|sql_data|knowledge_pack|untrusted_comments|unresolved)(?:\s+[^>]*)?>",
        r"[ESCAPED_TAG:\1]",
        content,
        flags=re.IGNORECASE,
    )
    # Neutralize any appearance of this request's exact boundary
    safe = safe.replace(f'id="{boundary_id}"', 'id="[ESCAPED]"').replace(
        f"id={boundary_id}", "id=[ESCAPED]"
    )
    return safe


def scan_canary_leak(output_text: str, canary_token: str) -> bool:
    """Returns True if the output leaked the secret canary token."""
    if not canary_token or not output_text:
        return False
    return canary_token in output_text
