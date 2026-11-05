"""Unit tests for final output guardrails: SQL safety, subset validation, glossary, and risk floor."""

import json
from pathlib import Path

from app.guardrails.final_output import (
    validate_glossary_membership,
    validate_literal_grounding,
    validate_risk_consistency_floor,
)
from app.guardrails.sql_safety import (
    validate_error_subset_of_opportunity,
    validate_generated_sql,
)
from app.knowledge.glossary import GlossaryHelper


def test_validate_generated_sql_safe() -> None:
    safe_sql = "SELECT MATNR, WERKS FROM MM_MARC_ECC_EU_R1_MARC_S WHERE LVORM = ''"
    verdict = validate_generated_sql(safe_sql, allowed_tables=["MM_MARC_ECC_EU_R1_MARC_S"])
    assert verdict.allowed is True
    assert not verdict.report.blocked


def test_validate_generated_sql_comments_blocked() -> None:
    sql_with_comment = "SELECT MATNR FROM MM_MARC_ECC_EU_R1_MARC_S -- comment prohibited"
    verdict = validate_generated_sql(sql_with_comment, allowed_tables=["MM_MARC_ECC_EU_R1_MARC_S"])
    assert verdict.allowed is False
    assert any(e.name == "sql_safety_comments_prohibited" for e in verdict.report.events)


def test_validate_generated_sql_ddl_blocked() -> None:
    ddl_sql = "DROP TABLE MM_MARC_ECC_EU_R1_MARC_S; SELECT 1"
    verdict = validate_generated_sql(ddl_sql, allowed_tables=["MM_MARC_ECC_EU_R1_MARC_S"])
    assert verdict.allowed is False
    assert any("disallowed" in e.name for e in verdict.report.events)


def test_validate_generated_sql_into_blocked() -> None:
    into_sql = "SELECT MATNR INTO #TempTable FROM MM_MARC_ECC_EU_R1_MARC_S"
    verdict = validate_generated_sql(into_sql, allowed_tables=["MM_MARC_ECC_EU_R1_MARC_S"])
    assert verdict.allowed is False
    assert any(e.name == "sql_safety_select_into_prohibited" for e in verdict.report.events)


def test_validate_generated_sql_unallowed_table_blocked() -> None:
    sql_unallowed = "SELECT MATNR FROM MM_UNALLOWED_ECC_EU_R1_TABLE_S"
    verdict = validate_generated_sql(sql_unallowed, allowed_tables=["MM_MARC_ECC_EU_R1_MARC_S"])
    assert verdict.allowed is False
    assert any(e.name == "sql_safety_unallowed_table" for e in verdict.report.events)


def test_validate_error_subset_on_real_examples() -> None:
    fixture_path = Path("backend/tests/fixtures/examples.json")
    if not fixture_path.exists():
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "examples.json"

    with open(fixture_path, "r", encoding="utf-8") as f:
        examples = json.load(f)

    for ex in examples:
        expected = ex.get("expected", {})
        opp_query = expected.get("Opportunity Query")
        err_query = expected.get("Error Query")
        if opp_query and err_query:
            verdict = validate_error_subset_of_opportunity(opp_query, err_query)
            assert verdict.allowed is True, (
                f"Example {ex.get('sheet_name')} failed subset validation: {verdict.report.events}"
            )


def test_validate_error_subset_mismatched_driving_table() -> None:
    opp = "SELECT MARC.MATNR FROM MM_MARC_ECC_EU_R1_MARC_S MARC WHERE MARC.WERKS = '1000'"
    err = "SELECT MARA.MATNR FROM MM_MARA_ECC_EU_R1_MARA_S MARA WHERE MARA.MTART = 'ROH'"
    verdict = validate_error_subset_of_opportunity(opp, err)
    assert verdict.allowed is False
    assert any(e.name == "subset_driving_table_mismatch" for e in verdict.report.events)


def test_validate_error_subset_missing_scope_predicate() -> None:
    opp = "SELECT MARC.MATNR FROM MM_MARC_ECC_EU_R1_MARC_S MARC WHERE MARC.WERKS = '1000' AND MARC.LVORM = ''"
    err = "SELECT MARC.MATNR FROM MM_MARC_ECC_EU_R1_MARC_S MARC WHERE MARC.WERKS = '1000' AND MARC.DISPO IS NULL"
    verdict = validate_error_subset_of_opportunity(opp, err)
    assert verdict.allowed is False
    assert any(e.name == "subset_predicates_missing" for e in verdict.report.events)


def test_literal_grounding() -> None:
    sql = "SELECT * FROM T WHERE STATUS = 'Z_UNKNOWN_STATUS' AND CODE = 999"
    known_tokens = {"1", "0", "STATUS", "CODE"}
    report = validate_literal_grounding(sql, known_tokens)
    assert any(e.name == "ungrounded_literal_warning" for e in report.events)


def test_risk_consistency_floor() -> None:
    synth_score = 0.2
    parser_floor = 0.75
    enforced_score, report = validate_risk_consistency_floor(synth_score, parser_floor)
    assert enforced_score == parser_floor
    assert any(e.name == "risk_score_floor_enforced" for e in report.events)


def test_glossary_membership() -> None:
    helper = GlossaryHelper()
    report_valid = validate_glossary_membership("subject_areas", "Finance", helper)
    assert len(report_valid.events) == 0

    report_invalid = validate_glossary_membership("subject_areas", "Invented_Area_99", helper)
    assert any(e.name == "glossary_term_unrecognized" for e in report_invalid.events)
