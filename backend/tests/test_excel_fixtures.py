"""Unit tests validating parser execution on all 11 Excel fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.parser.naming import SynitiNamingEngine
from app.parser.sql_parser import SqlParser


@pytest.fixture(scope="module")
def examples() -> list[dict]:
    fixtures_path = Path(__file__).resolve().parent / "fixtures" / "examples.json"
    if not fixtures_path.exists():
        pytest.skip("examples.json fixture not found (proprietary fixture excluded from git)")
    with open(fixtures_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def parser() -> SqlParser:
    return SqlParser()


@pytest.fixture(scope="module")
def naming_engine() -> SynitiNamingEngine:
    return SynitiNamingEngine()


def test_all_11_excel_inputs_parse_full_quality(examples: list[dict], parser: SqlParser) -> None:
    assert len(examples) == 11, "Must contain exactly 11 fixture examples"
    for ex in examples:
        parsed = parser.parse(ex["input_sql"])
        assert parsed.parse_quality == "full", (
            f"{ex['id']} failed to parse with full quality: {parsed.warnings}"
        )


def test_sheet1_naming_and_updates(examples: list[dict], parser: SqlParser) -> None:
    ex1 = examples[0]
    parsed = parser.parse(ex1["input_sql"])
    assert parsed.view_name == "MM_MARC_ECC_EU_R1_MARC_S_UPD_ZSTRGR"
    assert parsed.view_naming is not None
    assert parsed.view_naming.prefix == "MM"
    assert parsed.view_naming.sap_table == "MARC"
    assert parsed.view_naming.zsource == "ECC_EU_R1"
    assert parsed.view_naming.action == "UPD"
    assert parsed.view_naming.field == "ZSTRGR"

    # Update targets: ZSTRGRNEW with kind case
    target_names = {u.new_alias: u for u in parsed.update_targets}
    assert "ZSTRGRNEW" in target_names
    assert target_names["ZSTRGRNEW"].expression_kind == "case"

    # Scope filter ZACTIVE = 1
    assert any("ZACTIVE = 1" in sf for sf in parsed.scope_filters)


def test_sheet5_constant_update(examples: list[dict], parser: SqlParser) -> None:
    ex5 = examples[4]  # Sheet 5
    parsed = parser.parse(ex5["input_sql"])
    assert parsed.view_name == "MM_MARC_ECC_EU_R1_MARC_S_UPD_ZSSQSS_QM999999"
    target_names = {u.new_alias: u for u in parsed.update_targets}
    assert "ZSSQSSNEW" in target_names
    assert target_names["ZSSQSSNEW"].expression_kind == "constant"


def test_sheet7_cte_and_row_number(examples: list[dict], parser: SqlParser) -> None:
    ex7 = examples[6]  # Sheet 7
    parsed = parser.parse(ex7["input_sql"])
    assert parsed.view_name == "MM_MARA_MATKL_SIEVO"
    assert len(parsed.ctes) >= 1
    assert parsed.ctes[0].name == "MATKL_DERIVED"
    assert len(parsed.window_functions) >= 1
    assert any("ROW_NUMBER()" in wf for wf in parsed.window_functions)

    # Tables: DATADUMP is external_source, XREF is mapping_reference
    table_map = {t.raw_name: t for t in parsed.tables}
    assert "MM_MATERIAL_SIEVO_DATADUMP" in table_map
    assert table_map["MM_MATERIAL_SIEVO_DATADUMP"].role == "external_source"
    assert "MM_MATKL_L_XREF" in table_map
    assert table_map["MM_MATKL_L_XREF"].role == "mapping_reference"


def test_sheet8_lowercase_violation(examples: list[dict], parser: SqlParser) -> None:
    ex8 = examples[7]  # Sheet 8
    parsed = parser.parse(ex8["input_sql"])
    assert parsed.view_name == "VM_BUT000_ECC_EU_R1_LFA1_S_UPD_zACTIVE"
    assert parsed.view_naming is not None
    codes = [v.code for v in parsed.view_naming.violations]
    assert "LOWERCASE" in codes


def test_sheet9_and_10_action_order_violation(examples: list[dict], parser: SqlParser) -> None:
    for idx in (8, 9):  # Sheets 9 and 10
        ex = examples[idx]
        parsed = parser.parse(ex["input_sql"])
        assert parsed.view_name == "VM_BUT100_ECC_EU_R1_LFA1_S_FLVN00_UPD_ZACTIVE"
        assert parsed.view_naming is not None
        codes = [v.code for v in parsed.view_naming.violations]
        assert "ACTION_ORDER" in codes
        assert parsed.view_naming.action == "UPD"
        assert parsed.view_naming.field == "ZACTIVE"


def test_sheet11_bracketed_identifiers_and_scope(examples: list[dict], parser: SqlParser) -> None:
    ex11 = examples[10]  # Sheet 11
    parsed = parser.parse(ex11["input_sql"])
    assert parsed.view_name == "VM_WYT3_ECC_EU_R1_WYT3_S_UPD_ZWERKS"

    # Bracketed column names preserved
    col_names = [c.column for c in parsed.columns]
    assert any("ESPHORA Plant" in col for col in col_names)
    assert any("Plant as-is" in col for col in col_names)

    # Scope filter detected
    assert any("MANUAL_TARGET_INSERT" in sf for sf in parsed.scope_filters)
    assert any("ZACTIVE = 1" in sf for sf in parsed.scope_filters)
