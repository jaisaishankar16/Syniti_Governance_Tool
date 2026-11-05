"""Unit tests for Query Complexity and Risk Analysis."""

from __future__ import annotations

from typing import cast

import sqlglot
from app.parser.models import TableInfo
from app.parser.risk import evaluate_complexity_and_risk
from sqlglot import exp


def test_select_star_risk() -> None:
    sql = "SELECT * FROM MARC"
    tree = sqlglot.parse_one(sql, read="tsql")
    comp, risks = evaluate_complexity_and_risk(
        expression=cast(exp.Expression, tree),
        raw_sql=sql,
        tables=[TableInfo(raw_name="MARC")],
        joins=[],
        filters=[],
        ctes=[],
        window_functions=[],
        set_operations=[],
    )
    assert comp.score >= 1
    codes = [r.code for r in risks]
    assert "SELECT_STAR" in codes
    assert "NO_WHERE" in codes


def test_non_sargable_predicate_risk() -> None:
    sql = "SELECT MATNR FROM MARC WHERE SUBSTRING(WERKS, 1, 2) = '10'"
    tree = sqlglot.parse_one(sql, read="tsql")
    comp, risks = evaluate_complexity_and_risk(
        expression=cast(exp.Expression, tree),
        raw_sql=sql,
        tables=[TableInfo(raw_name="MARC")],
        joins=[],
        filters=["SUBSTRING(WERKS, 1, 2) = '10'"],
        ctes=[],
        window_functions=[],
        set_operations=[],
    )
    assert comp.score >= 1
    codes = [r.code for r in risks]
    assert "NON_SARGABLE_PREDICATE" in codes


def test_case_without_else_risk() -> None:
    sql = "SELECT CASE WHEN ZACTIVE = 1 THEN 'Active' END FROM MARC WHERE ZACTIVE = 1"
    tree = sqlglot.parse_one(sql, read="tsql")
    comp, risks = evaluate_complexity_and_risk(
        expression=cast(exp.Expression, tree),
        raw_sql=sql,
        tables=[TableInfo(raw_name="MARC")],
        joins=[],
        filters=["ZACTIVE = 1"],
        ctes=[],
        window_functions=[],
        set_operations=[],
    )
    assert comp.score >= 1
    codes = [r.code for r in risks]
    assert "CASE_WITHOUT_ELSE" in codes


def test_null_string_concat_risk() -> None:
    sql = "SELECT A + '0A' AS derived FROM MARC WHERE ZACTIVE = 1"
    tree = sqlglot.parse_one(sql, read="tsql")
    comp, risks = evaluate_complexity_and_risk(
        expression=cast(exp.Expression, tree),
        raw_sql=sql,
        tables=[TableInfo(raw_name="MARC")],
        joins=[],
        filters=["ZACTIVE = 1"],
        ctes=[],
        window_functions=[],
        set_operations=[],
    )
    assert comp.score >= 1
    codes = [r.code for r in risks]
    assert "NULL_STRING_CONCAT" in codes


def test_complexity_scoring() -> None:
    sql = """
    WITH Cte1 AS (SELECT 1 AS n)
    SELECT n FROM Cte1
    UNION
    SELECT 2
    """
    tree = sqlglot.parse_one(sql, read="tsql")
    comp, _risks = evaluate_complexity_and_risk(
        expression=cast(exp.Expression, tree),
        raw_sql=sql,
        tables=[],
        joins=[],
        filters=[],
        ctes=[type("CTE", (), {"recursive": False})],
        window_functions=[],
        set_operations=["UNION"],
    )
    assert comp.score >= 3
    assert comp.level in ("low", "medium")
