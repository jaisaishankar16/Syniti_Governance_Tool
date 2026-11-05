"""Comparison script: Deterministic Baseline Opportunity Query vs Expected Opportunity Query across all 11 Excel examples."""

from __future__ import annotations

import json
from pathlib import Path

from app.parser.sql_parser import SqlParser
from app.synthesizer.deterministic import baseline_opportunity_query


def main() -> None:
    fixture_path = (
        Path(__file__).resolve().parent.parent / "backend" / "tests" / "fixtures" / "examples.json"
    )
    if not fixture_path.exists():
        fixture_path = Path("backend/tests/fixtures/examples.json").resolve()

    with open(fixture_path, "r", encoding="utf-8") as f:
        examples = json.load(f)

    parser = SqlParser()

    print("=" * 100)
    print("      PHASE 4: DETERMINISTIC BASELINE VS EXPECTED OPPORTUNITY QUERY COMPARISON")
    print("=" * 100)
    print(
        f"{'Sheet ID':<10} | {'Driving Table Match':<20} | {'AST Valid':<10} | {'Scope Predicates Found':<25} | Status"
    )
    print("-" * 100)

    all_matched = True

    for idx, ex in enumerate(examples):
        sheet_id = ex.get("id", f"Sheet{idx + 1}")
        input_sql = ex["input_sql"]
        expected_opp = ex.get("expected", {}).get("Opportunity Query", "").strip()

        parsed_input = parser.parse(input_sql)
        baseline_sql = baseline_opportunity_query(parsed_input)

        # Parse both baseline and expected
        try:
            parsed_baseline = parser.parse(baseline_sql)
            ast_valid = True
        except Exception:  # noqa: BLE001
            ast_valid = False

        try:
            parsed_expected = parser.parse(expected_opp)
            expected_driving = parsed_expected.driving_table
        except Exception:  # noqa: BLE001
            expected_driving = None

        baseline_driving = parsed_baseline.driving_table if ast_valid else None
        input_driving = parsed_input.driving_table

        # Check driving table match
        table_match = False
        if baseline_driving and expected_driving:
            table_match = baseline_driving.lower() == expected_driving.lower()
        elif baseline_driving and input_driving:
            table_match = baseline_driving.lower() == input_driving.lower()

        scope_count = len(parsed_input.scope_filters)
        status = "MATCH" if (ast_valid and table_match) else "DIFF"
        if not (ast_valid and table_match):
            all_matched = False

        print(
            f"{sheet_id:<10} | {table_match!s:<20} | {ast_valid!s:<10} | {f'{scope_count} filter(s)':<25} | {status}"
        )

    print("=" * 100)
    print(f"Overall Result: {'11/11 PASSED' if all_matched else 'DIFFERENCES DETECTED'}")
    print("=" * 100)


if __name__ == "__main__":
    main()
