"""SQL safety validation for generated Opportunity and Error queries, and subset verification."""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from app.guardrails.models import GuardrailEvent, GuardrailReport, KeyValue, Verdict

DISALLOWED_COMMAND_REGEX = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|WAITFOR|OPENROWSET|OPENQUERY|OPENDATASOURCE|BULK)\b",
    re.IGNORECASE,
)
SYSTEM_CALL_REGEX = re.compile(r"\b(sp_|xp_|xp_cmdshell)\b", re.IGNORECASE)


def _normalize_predicate(node: exp.Expression) -> str:
    """Normalizes a WHERE predicate by stripping table qualifiers and spaces."""
    clone = node.copy()
    for col in clone.find_all(exp.Column):
        col.set("table", None)
    return (
        clone.sql("tsql")
        .upper()
        .replace(" ", "")
        .replace('"', "")
        .replace("[", "")
        .replace("]", "")
    )


def _flatten_and(node: exp.Expression | None) -> list[exp.Expression]:
    """Recursively flattens an AND expression into its constituent terms."""
    if node is None:
        return []
    if isinstance(node, exp.And):
        left = getattr(node, "left", None)
        right = getattr(node, "right", None)
        return _flatten_and(left) + _flatten_and(right)
    return [node]


def validate_generated_sql(
    sql: str, allowed_tables: list[str], request_id: str = ""
) -> Verdict[str]:
    """Validates that a generated query is a safe, read-only, single-statement SELECT referencing only allowed tables."""
    report = GuardrailReport()
    clean_sql = sql.strip().rstrip(";")

    # 1. Reject comments
    if re.search(r"(?:--[^\r\n]*|/\*[\s\S]*?\*/)", clean_sql):
        report.add_event(
            GuardrailEvent(
                name="sql_safety_comments_prohibited",
                stage="final_output",
                action="block",
                severity="high",
                message="Generated SQL contains comments, which are prohibited in rule queries",
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=clean_sql, report=report)

    # 2. Reject dangerous commands via fast regex check
    cmd_match = DISALLOWED_COMMAND_REGEX.search(clean_sql)
    if cmd_match:
        disallowed_word = cmd_match.group(1).upper()
        report.add_event(
            GuardrailEvent(
                name="sql_safety_disallowed_command",
                stage="final_output",
                action="block",
                severity="critical",
                message=f"Generated SQL contains disallowed command '{disallowed_word}'",
                details=[KeyValue(key="command", value=disallowed_word)],
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=clean_sql, report=report)

    # 3. Reject system procedure calls
    sys_match = SYSTEM_CALL_REGEX.search(clean_sql)
    if sys_match:
        proc_word = sys_match.group(1)
        report.add_event(
            GuardrailEvent(
                name="sql_safety_system_call_prohibited",
                stage="final_output",
                action="block",
                severity="critical",
                message=f"Generated SQL contains system procedure reference '{proc_word}'",
                details=[KeyValue(key="procedure", value=proc_word)],
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=clean_sql, report=report)

    # 4. Parse with sqlglot
    try:
        parsed_statements = sqlglot.parse(clean_sql, read="tsql")
    except Exception as exc:  # noqa: BLE001
        report.add_event(
            GuardrailEvent(
                name="sql_safety_parse_error",
                stage="final_output",
                action="block",
                severity="high",
                message=f"Generated SQL failed syntax parsing: {exc}",
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=clean_sql, report=report)

    if len(parsed_statements) != 1 or parsed_statements[0] is None:
        report.add_event(
            GuardrailEvent(
                name="sql_safety_multiple_statements",
                stage="final_output",
                action="block",
                severity="critical",
                message=f"Generated SQL must be exactly one statement (found {len(parsed_statements)})",
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=clean_sql, report=report)

    ast = parsed_statements[0]

    # 5. Must be a SELECT or CTE WITH ... SELECT
    is_select = isinstance(ast, (exp.Select, exp.Union)) or (
        isinstance(ast, exp.Expression) and bool(ast.find(exp.Select))
    )
    if not is_select:
        report.add_event(
            GuardrailEvent(
                name="sql_safety_not_select",
                stage="final_output",
                action="block",
                severity="critical",
                message=f"Generated SQL is not a read-only SELECT query (found {type(ast).__name__})",
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=clean_sql, report=report)

    # 6. Reject SELECT ... INTO
    if ast.find(exp.Into):
        report.add_event(
            GuardrailEvent(
                name="sql_safety_select_into_prohibited",
                stage="final_output",
                action="block",
                severity="critical",
                message="SELECT ... INTO syntax is prohibited in generated queries",
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=clean_sql, report=report)

    # 7. Allowed table validation
    normalized_allowed = {
        tbl.split(".")[-1].replace("[", "").replace("]", "").upper() for tbl in allowed_tables
    }
    for tbl_node in ast.find_all(exp.Table):
        t_name = tbl_node.name.replace("[", "").replace("]", "").upper()
        if t_name and t_name not in normalized_allowed:
            report.add_event(
                GuardrailEvent(
                    name="sql_safety_unallowed_table",
                    stage="final_output",
                    action="block",
                    severity="high",
                    message=f"Generated SQL references table '{t_name}' not in allowed tables",
                    details=[KeyValue(key="table", value=t_name)],
                    request_id=request_id,
                )
            )
            return Verdict(allowed=False, value=clean_sql, report=report)

    report.add_event(
        GuardrailEvent(
            name="sql_safety_validated",
            stage="final_output",
            action="pass",
            severity="info",
            message="Generated SQL passed read-only and table scope validation",
            request_id=request_id,
        )
    )
    return Verdict(allowed=True, value=clean_sql, report=report)


def validate_error_subset_of_opportunity(
    opp_sql: str, err_sql: str, request_id: str = ""
) -> Verdict[bool]:
    """Validates that Error query is a strict subset of Opportunity query (same driving table & preserves scope predicates)."""
    report = GuardrailReport()

    try:
        opp_ast = sqlglot.parse_one(opp_sql.strip().rstrip(";"), read="tsql")
        err_ast = sqlglot.parse_one(err_sql.strip().rstrip(";"), read="tsql")
    except Exception as exc:  # noqa: BLE001
        report.add_event(
            GuardrailEvent(
                name="subset_parse_error",
                stage="final_output",
                action="block",
                severity="high",
                message=f"Failed parsing opportunity or error query for subset validation: {exc}",
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=False, report=report)

    # 1. Driving table comparison (first table in FROM)
    opp_table_node = opp_ast.find(exp.Table)
    err_table_node = err_ast.find(exp.Table)

    if not opp_table_node or not err_table_node:
        report.add_event(
            GuardrailEvent(
                name="subset_missing_driving_table",
                stage="final_output",
                action="block",
                severity="high",
                message="Could not resolve driving table in opportunity or error query",
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=False, report=report)

    opp_driving = opp_table_node.name.replace("[", "").replace("]", "").upper()
    err_driving = err_table_node.name.replace("[", "").replace("]", "").upper()

    if opp_driving != err_driving:
        report.add_event(
            GuardrailEvent(
                name="subset_driving_table_mismatch",
                stage="final_output",
                action="block",
                severity="critical",
                message=f"Driving table mismatch: Opportunity is '{opp_driving}' but Error is '{err_driving}'",
                details=[
                    KeyValue(key="opp_table", value=opp_driving),
                    KeyValue(key="err_table", value=err_driving),
                ],
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=False, report=report)

    # 2. Scope predicates subset check
    opp_where = opp_ast.find(exp.Where)
    err_where = err_ast.find(exp.Where)

    opp_preds = {
        _normalize_predicate(p) for p in _flatten_and(opp_where.this if opp_where else None)
    }

    err_preds = {
        _normalize_predicate(p) for p in _flatten_and(err_where.this if err_where else None)
    }

    # Every opportunity scope predicate must be in error query predicates
    missing_scope_preds = opp_preds - err_preds
    if missing_scope_preds:
        report.add_event(
            GuardrailEvent(
                name="subset_predicates_missing",
                stage="final_output",
                action="block",
                severity="high",
                message=f"Error query is missing opportunity scope predicates: {', '.join(missing_scope_preds)}",
                details=[KeyValue(key="missing_predicate", value=p) for p in missing_scope_preds],
                request_id=request_id,
            )
        )
        return Verdict(allowed=False, value=False, report=report)

    report.add_event(
        GuardrailEvent(
            name="subset_validated",
            stage="final_output",
            action="pass",
            severity="info",
            message=f"Error query correctly verified as a subset of opportunity query on '{opp_driving}'",
            request_id=request_id,
        )
    )
    return Verdict(allowed=True, value=True, report=report)
