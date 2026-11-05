"""Tests for the Phase 4 deterministic layer (baseline opportunity query, risk floor)."""

from __future__ import annotations

import json
from pathlib import Path

from app.parser.sql_parser import SqlParser
from app.synthesizer.deterministic import (
    baseline_opportunity_query,
    risk_floor,
)


def _load_excel_fixtures() -> list[dict]:
    p = Path(__file__).resolve().parent / "fixtures" / "examples.json"
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


class TestDeterministicBaselines:
    """Verifies deterministic opportunity generation across all 11 Excel examples."""

    def test_all_11_examples_produce_valid_baseline_opportunity(self) -> None:
        examples = _load_excel_fixtures()
        assert len(examples) == 11, f"Expected 11 examples, found {len(examples)}"

        parser = SqlParser()
        for idx, ex in enumerate(examples):
            sheet = ex.get("source_sheet", f"sheet_{idx}")
            input_sql = ex["input_sql"]

            parsed = parser.parse(input_sql)
            baseline_sql = baseline_opportunity_query(parsed)

            assert baseline_sql is not None, f"Baseline SQL for {sheet} was None"
            assert baseline_sql.strip().upper().startswith("SELECT"), (
                f"Baseline SQL for {sheet} does not start with SELECT: {baseline_sql}"
            )

            # Baseline query must be syntactically valid T-SQL
            parsed_baseline = parser.parse(baseline_sql)
            assert parsed_baseline.driving_table is not None, (
                f"Parsed baseline for {sheet} has no driving table"
            )

            # Verify driving table matches the original query's driving table (or underlying physical table if CTE)
            if parsed.driving_table:
                orig_driving = parsed.driving_table
                base_driving = parsed_baseline.driving_table
                cte_names = {c.name.lower() for c in parsed.ctes}
                if orig_driving.lower() in cte_names:
                    physical_tables = [
                        t.raw_name.lower()
                        for t in parsed.tables
                        if t.raw_name.lower() not in cte_names
                    ]
                    assert base_driving.lower() in physical_tables, (
                        f"Unwrapped CTE driving table {base_driving} not in physical tables {physical_tables}"
                    )
                else:
                    assert orig_driving.lower() == base_driving.lower(), (
                        f"Driving table mismatch for {sheet}: original={orig_driving}, baseline={base_driving}"
                    )


class TestRiskFloor:
    """Tests deterministic risk flooring against SQL antipatterns and naming violations."""

    def test_risk_floor_elevated_on_select_star(self) -> None:
        parser = SqlParser()
        parsed = parser.parse("SELECT * FROM [MM_MARC_ECC_EU_R1_MARC_S]")
        rf = risk_floor(parsed)
        levels = ["low", "medium", "high", "critical"]
        assert levels.index(rf) >= levels.index("medium")

    def test_risk_floor_clean_query(self) -> None:
        parser = SqlParser()
        parsed = parser.parse(
            "SELECT [MATNR], [WERKS] FROM [MM_MARC_ECC_EU_R1_MARC_S] WHERE [WERKS] = '1000'"
        )
        rf = risk_floor(parsed)
        assert rf in ("low", "medium")
