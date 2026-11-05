"""Deterministic query synthesis, risk flooring, and baseline reference query construction."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from app.guardrails.sql_safety import validate_generated_sql

if TYPE_CHECKING:
    from app.parser.models import ParsedQuery

logger = logging.getLogger(__name__)


def risk_floor(parsed: ParsedQuery) -> Literal["low", "medium", "high", "critical"]:
    """Computes the deterministic minimum risk severity floor implied by parser risk flags."""
    severities = {f.severity.lower() for f in parsed.risk_flags}

    if "critical" in severities:
        return "critical"
    if "high" in severities or parsed.complexity.level == "critical":
        return "high"
    if (
        "medium" in severities
        or "warning" in severities
        or parsed.complexity.level in ("high", "medium")
    ):
        return "medium"
    return "low"


def baseline_opportunity_query(parsed: ParsedQuery) -> str:
    """Builds a deterministic baseline Opportunity Query from parsed driving table and scope filters.

    Under Project Rules (see docs/PROJECT_RULES.md), this baseline query serves strictly as a validation
    reference benchmark and fewshot prompt reference, never as a user-facing fallback or direct substitution.
    """
    # Resolve driving table name
    driving_tbl = parsed.driving_table
    cte_names = {c.name.lower() for c in parsed.ctes}

    # If driving_tbl is a CTE, unwrap to the underlying physical source/staging table
    if driving_tbl and driving_tbl.lower() in cte_names:
        for t in parsed.tables:
            t_name = t.raw_name.lower()
            if t_name not in cte_names and getattr(t, "role", "") != "mapping_reference":
                driving_tbl = t.raw_name
                break

    if not driving_tbl and parsed.tables:
        for t in parsed.tables:
            t_name = t.raw_name.lower()
            if t_name not in cte_names and getattr(t, "role", "") != "mapping_reference":
                driving_tbl = t.raw_name
                break
        if not driving_tbl:
            driving_tbl = parsed.tables[0].raw_name

    if not driving_tbl:
        return "SELECT 1"

    # Match driving table info to check alias
    matching_info = next(
        (t for t in parsed.tables if t.raw_name == driving_tbl or t.alias == driving_tbl),
        None,
    )
    tbl_raw_name = matching_info.raw_name if matching_info else driving_tbl
    tbl_alias = matching_info.alias if matching_info else None

    from_clause = f"FROM {tbl_raw_name} {tbl_alias}" if tbl_alias else f"FROM {tbl_raw_name}"

    # Compile scope predicates
    predicates = list(parsed.scope_filters)
    where_clause = ""
    if predicates:
        where_clause = " WHERE " + " AND ".join(predicates)

    query = f"SELECT * {from_clause}{where_clause}"

    # Validate generated SQL for safety
    verdict = validate_generated_sql(query, allowed_tables=[tbl_raw_name])
    if not verdict.allowed:
        # Minimal safe query on driving table
        return f"SELECT * FROM {tbl_raw_name}"

    return query
