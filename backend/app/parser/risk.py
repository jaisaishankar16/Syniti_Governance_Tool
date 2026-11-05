"""Complexity and Risk analysis for T-SQL queries."""

from __future__ import annotations

import logging
from typing import Any, Literal

from sqlglot import exp

from .models import Complexity, RiskFlag

logger = logging.getLogger(__name__)


def evaluate_complexity_and_risk(
    expression: exp.Expression | None,
    raw_sql: str,
    tables: list[Any],
    joins: list[Any],
    filters: list[str],
    ctes: list[Any],
    window_functions: list[str],
    set_operations: list[str],
    statement_count: int = 1,
) -> tuple[Complexity, list[RiskFlag]]:
    """Evaluates query complexity (1-10) and detects potential risk flags."""
    factors: list[str] = []
    score = 1

    # 1. Joins complexity
    join_count = len(joins)
    if join_count > 0:
        added = min(join_count, 3)
        score += added
        factors.append(f"{join_count} join(s) (+{added})")

    # 2. CTEs
    cte_count = len(ctes)
    if cte_count > 0:
        added = min(cte_count, 2)
        has_recursive = any(getattr(c, "recursive", False) for c in ctes)
        if has_recursive:
            added += 1
            factors.append(f"{cte_count} CTE(s) with recursion (+{added})")
        else:
            factors.append(f"{cte_count} CTE(s) (+{added})")
        score += added

    # 3. Window functions
    if window_functions:
        score += 1
        factors.append(f"{len(window_functions)} window function(s) (+1)")

    # 4. Set operations (UNION/EXCEPT/INTERSECT)
    if set_operations:
        score += 1
        factors.append(f"{len(set_operations)} set operation(s) (+1)")

    # 5. Statements count
    if statement_count > 1:
        score += 1
        factors.append(f"Multi-statement batch ({statement_count} statements) (+1)")

    # 6. CASE expressions depth
    case_nodes = list(expression.find_all(exp.Case)) if expression else []
    if case_nodes:
        score += 1
        max_branches = max(len(c.args.get("ifs", [])) for c in case_nodes)
        if max_branches > 3:
            score += 1
            factors.append(f"Complex CASE expressions with {max_branches} branches (+2)")
        else:
            factors.append("CASE expression(s) present (+1)")

    # 7. Subqueries / nesting
    subqueries = list(expression.find_all(exp.Select)) if expression else []
    if len(subqueries) > 1:
        score += 1
        factors.append(f"Nested subqueries ({len(subqueries) - 1}) (+1)")

    # Clamp score to 1-10
    score = max(1, min(score, 10))

    level: Literal["low", "medium", "high", "critical"]
    if score <= 3:
        level = "low"
    elif score <= 6:
        level = "medium"
    elif score <= 8:
        level = "high"
    else:
        level = "critical"

    complexity = Complexity(score=score, level=level, factors=factors)

    # Risk Flags Evaluation
    risk_flags: list[RiskFlag] = []
    upper_sql = raw_sql.upper()

    if expression is not None:
        # 1. SELECT *
        has_star = False
        for select_node in expression.find_all(exp.Select):
            for e in select_node.expressions:
                if isinstance(e, exp.Star):
                    has_star = True
                    break
        if has_star:
            risk_flags.append(
                RiskFlag(
                    code="SELECT_STAR",
                    severity="warning",
                    message="Query contains 'SELECT *' projection which risks column drift and unnecessary payload",
                )
            )

        # 2. NO WHERE clause (on staging/production tables)
        has_tables = len(tables) > 0
        has_where = expression.find(exp.Where) is not None
        if has_tables and not has_where:
            risk_flags.append(
                RiskFlag(
                    code="NO_WHERE",
                    severity="warning",
                    message="Query queries staging/native tables without a WHERE filtering clause",
                )
            )

        # 3. Cartesian or missing join condition
        for j in expression.find_all(exp.Join):
            kind = (j.args.get("kind") or "").upper()
            on_clause = j.args.get("on")
            if "CROSS" in kind or (not on_clause and kind not in ("NATURAL",)):
                risk_flags.append(
                    RiskFlag(
                        code="CARTESIAN_JOIN",
                        severity="high",
                        message="Potential cartesian join detected without ON predicate",
                    )
                )
                break

        # 4. Functions on filter columns (Non-sargable)
        non_sargable_found = False
        for w in expression.find_all(exp.Where):
            for col in w.find_all(exp.Column):
                parent = col.parent
                if isinstance(
                    parent, (exp.Func, exp.Anonymous, exp.Substring, exp.Upper, exp.Lower, exp.Cast)
                ):
                    non_sargable_found = True
                    break
        if not non_sargable_found:
            for j in expression.find_all(exp.Join):
                on = j.args.get("on")
                if on:
                    for col in on.find_all(exp.Column):
                        parent = col.parent
                        if isinstance(
                            parent,
                            (
                                exp.Func,
                                exp.Anonymous,
                                exp.Substring,
                                exp.Upper,
                                exp.Lower,
                                exp.Cast,
                            ),
                        ):
                            non_sargable_found = True
                            break
        if non_sargable_found:
            risk_flags.append(
                RiskFlag(
                    code="NON_SARGABLE_PREDICATE",
                    severity="medium",
                    message="Function or transformation applied to column in predicate/join, preventing index seek (non-sargable)",
                )
            )

        # 5. NOLOCK hint
        if "NOLOCK" in upper_sql:
            risk_flags.append(
                RiskFlag(
                    code="NOLOCK_HINT",
                    severity="info",
                    message="NOLOCK table hint present; query may read uncommitted/dirty records",
                )
            )

        # 6. NOT IN with subquery/nullable
        for in_expr in expression.find_all(exp.In):
            if in_expr.args.get("is_not") or "NOT IN" in str(in_expr).upper():
                risk_flags.append(
                    RiskFlag(
                        code="NOT_IN_NULLABLE",
                        severity="medium",
                        message="'NOT IN' predicate used; will return zero rows if the set contains NULL values",
                    )
                )
                break

        # 7. String '+' concatenation that can yield NULL
        plus_concat = False
        for b in expression.find_all(exp.Add):
            left_str = isinstance(b.this, exp.Literal) and b.this.is_string
            right_str = isinstance(b.expression, exp.Literal) and b.expression.is_string
            if left_str or right_str:
                plus_concat = True
                break
        if plus_concat or (" + '" in raw_sql or "' + " in raw_sql):
            risk_flags.append(
                RiskFlag(
                    code="NULL_STRING_CONCAT",
                    severity="medium",
                    message="T-SQL string concatenation with '+' operator without COALESCE/ISNULL will result in NULL if any operand is NULL",
                )
            )

        # 8. ROW_NUMBER without deterministic ORDER BY
        for win in expression.find_all(exp.Window):
            parent = win.parent
            is_row_number = False
            if isinstance(parent, (exp.Anonymous, exp.Func)):
                if getattr(parent, "name", "").upper() == "ROW_NUMBER":
                    is_row_number = True
            elif "ROW_NUMBER" in str(win).upper():
                is_row_number = True

            if is_row_number:
                order_spec = win.args.get("order")
                if (
                    not order_spec
                    or not str(order_spec).strip()
                    or "NULL" in str(order_spec).upper()
                ):
                    risk_flags.append(
                        RiskFlag(
                            code="ROW_NUMBER_NON_DETERMINISTIC",
                            severity="high",
                            message="ROW_NUMBER() window function used without deterministic ORDER BY clause",
                        )
                    )
                    break

        # 9. No MANDT filter for SAP tables
        sap_staging_or_native = any(
            getattr(t, "role", "") in ("sap_staging", "sap_native")
            or (getattr(t, "naming", None) and getattr(t.naming, "sap_table", None))
            for t in tables
        )
        has_mandt_filter = any("MANDT" in (f or "").upper() for f in filters)
        if sap_staging_or_native and not has_mandt_filter:
            risk_flags.append(
                RiskFlag(
                    code="NO_MANDT_FILTER",
                    severity="low",
                    message="No MANDT (SAP Client) filter found on SAP staging/native table(s)",
                )
            )

        # 10. CASE without ELSE
        for case in case_nodes:
            if case.args.get("default") is None:
                risk_flags.append(
                    RiskFlag(
                        code="CASE_WITHOUT_ELSE",
                        severity="medium",
                        message="CASE expression has no ELSE branch; non-matching conditions will yield NULL",
                    )
                )
                break

    return complexity, risk_flags
