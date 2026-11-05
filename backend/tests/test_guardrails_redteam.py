"""Data-driven test suite executing all cases from backend/tests/fixtures/redteam.json."""

import json
from pathlib import Path
from typing import Any

import pytest
from app.guardrails.models import GuardrailBlocked
from app.guardrails.pipeline import pre_flight_pipeline
from app.guardrails.sql_safety import (
    validate_error_subset_of_opportunity,
    validate_generated_sql,
)


def load_redteam_cases() -> list[dict[str, Any]]:
    fixture_path = Path("backend/tests/fixtures/redteam.json")
    if not fixture_path.exists():
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "redteam.json"

    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        return data.get("cases", [])


@pytest.mark.parametrize("case", load_redteam_cases(), ids=lambda c: str(c.get("id")))
def test_redteam_case(case: dict[str, Any]) -> None:
    case_id = case["id"]
    category = case["category"]
    stage = case.get("expected_stage")
    expected_event = case.get("expected_event")
    expected_action = case.get("expected_action")

    if stage == "input":
        input_sql = case["input_sql"]
        # Pre-flight may raise GuardrailBlocked or return SanitizedInput with report events
        try:
            res = pre_flight_pipeline(input_sql)
            report = res.report
        except GuardrailBlocked as exc:
            report = exc.report

        all_event_names = [e.name for e in report.events]
        matching_event = next((e for e in report.events if expected_event in e.name), None)
        assert matching_event is not None, (
            f"Case {case_id} failed: expected event containing '{expected_event}', got {all_event_names}"
        )
        if expected_action:
            assert matching_event.action == expected_action, (
                f"Case {case_id}: expected action '{expected_action}', got '{matching_event.action}'"
            )

    elif stage == "final_output":
        if category == "subset_validation":
            opp_sql = case["opportunity_sql"]
            err_sql = case["error_sql"]
            verdict = validate_error_subset_of_opportunity(opp_sql, err_sql)
            assert verdict.allowed is False, f"Case {case_id} unexpectedly allowed subset"
            all_event_names = [e.name for e in verdict.report.events]
            assert any(expected_event in e_name for e_name in all_event_names), (
                f"Case {case_id}: expected event containing '{expected_event}', got {all_event_names}"
            )
        else:
            input_sql = case["input_sql"]
            verdict = validate_generated_sql(input_sql, allowed_tables=["MM_MARC_ECC_EU_R1_MARC_S"])
            assert verdict.allowed is False, f"Case {case_id} unexpectedly allowed SQL"
            all_event_names = [e.name for e in verdict.report.events]
            assert any(expected_event in e_name for e_name in all_event_names), (
                f"Case {case_id}: expected event containing '{expected_event}', got {all_event_names}"
            )
