"""Unit tests for deterministic knowledge packs."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.knowledge_pack import build_sap_pack, build_sql_pack
from app.knowledge.store import KnowledgeStore
from app.parser.sql_parser import SqlParser


def test_11_examples_sap_pack_coverage():
    """For all 11 Excel fixture examples, every resolved SAP table appears in resolved."""
    fixtures_path = Path(__file__).resolve().parent / "fixtures" / "examples.json"
    if not fixtures_path.exists():
        fixtures_path = Path("backend/tests/fixtures/examples.json").resolve()
    if not fixtures_path.exists():
        import pytest

        pytest.skip("examples.json fixture not found (proprietary fixture excluded from git)")

    with open(fixtures_path, "r", encoding="utf-8") as f:
        examples = json.load(f)

    parser = SqlParser()
    store = KnowledgeStore()

    for ex in examples:
        sql = ex["input_sql"]
        parsed = parser.parse(sql)
        pack = build_sap_pack(parsed, store)

        resolved_table_names = {r.table for r in pack.resolved}

        # Check view SAP table
        if parsed.view_naming and parsed.view_naming.sap_table:
            assert parsed.view_naming.sap_table in resolved_table_names

        # Check tables used
        for t in parsed.tables:
            if t.role == "sap_staging" and t.sap_table:
                assert t.sap_table in resolved_table_names, (
                    f"Table {t.sap_table} missing from resolved in sheet {ex.get('source_sheet')}"
                )


def test_mapping_reference_tables_never_appear_as_sap_lookups():
    """Mapping and reference tables (e.g. XREF, SIEVO) are never looked up as SAP standard tables."""
    sql = """
    SELECT s.MATNR, m.TARGET_VAL
    FROM MM_MARC_ECC_EU_R1_MARC_S s
    INNER JOIN XREF_PLANT_MAPPING m ON s.WERKS = m.SOURCE_VAL
    WHERE s.ZACTIVE = 1
    """
    parser = SqlParser()
    store = KnowledgeStore()
    parsed = parser.parse(sql)

    pack = build_sap_pack(parsed, store)

    # XREF table must be listed in mapping_reference_tables
    assert any("XREF" in name for name in pack.mapping_reference_tables)

    # Must NOT be in resolved or unresolved SAP tables
    for item in pack.resolved:
        assert "XREF" not in item.table
    for item in pack.unresolved:
        assert "XREF" not in item.table


def test_unknown_z_fields_land_in_unresolved():
    """Unknown Z fields without a convention land in unresolved list (not guessed)."""
    sql = "SELECT ZUNKNOWN_RANDOM_COLUMN FROM MM_MARA_ECC_EU_R1_MARA_S"
    parser = SqlParser()
    store = KnowledgeStore()
    parsed = parser.parse(sql)

    pack = build_sap_pack(parsed, store)
    unresolved_fields = [u.field for u in pack.unresolved]
    assert "ZUNKNOWN_RANDOM_COLUMN" in unresolved_fields


def test_pack_max_items_limit():
    """Packs strictly respect PACK_MAX_ITEMS."""
    sql = "SELECT MATNR, WERKS, ZACTIVE, 1 AS FLAGNEW FROM MM_MARC_ECC_EU_R1_MARC_S"
    parser = SqlParser()
    store = KnowledgeStore()
    parsed = parser.parse(sql)

    sap_pack = build_sap_pack(parsed, store, max_items=2)
    assert len(sap_pack.resolved) <= 2
    assert len(sap_pack.unresolved) <= 2

    sql_pack = build_sql_pack(parsed, store, max_items=2)
    assert len(sql_pack.resolved) <= 2
    assert len(sql_pack.unresolved) <= 2


def test_sheet1_sap_pack_fixes():
    """Sheet 1 verification:
    - MTART resolves against MARA
    - ZSTRGRNEW resolves by convention (custom_convention)
    - source_fields in update_targets has no duplicates (['ZWERKS', 'MTART'])
    - mandt_note matches knowledge file directly
    - unresolved is empty
    """
    fixtures_path = Path(__file__).resolve().parent / "fixtures" / "examples.json"
    if not fixtures_path.exists():
        fixtures_path = Path("backend/tests/fixtures/examples.json").resolve()
    if not fixtures_path.exists():
        import pytest

        pytest.skip("examples.json fixture not found (proprietary fixture excluded from git)")

    with open(fixtures_path, "r", encoding="utf-8") as f:
        examples = json.load(f)

    sheet1_sql = examples[0]["input_sql"]
    parser = SqlParser()
    store = KnowledgeStore()
    parsed = parser.parse(sheet1_sql)

    pack = build_sap_pack(parsed, store)

    # 1. MTART resolves against MARA
    mtart_items = [r for r in pack.resolved if r.field == "MTART"]
    assert len(mtart_items) == 1, "MTART should be resolved"
    assert mtart_items[0].table == "MARA", f"Expected MTART table MARA, got {mtart_items[0].table}"
    assert mtart_items[0].source in ("prebuilt", "common_fields")

    # 2. ZSTRGRNEW resolves by convention
    znew_items = [r for r in pack.resolved if r.field == "ZSTRGRNEW"]
    assert len(znew_items) == 1, "ZSTRGRNEW should be resolved by convention"
    assert znew_items[0].source == "custom_convention"
    assert "derived target value for ZSTRGR" in znew_items[0].description

    # 3. source_fields has no duplicates
    assert len(pack.update_targets) == 1
    source_fields = pack.update_targets[0]["source_fields"]
    assert source_fields == ["ZWERKS", "MTART"], (
        f"Expected ['ZWERKS', 'MTART'], got {source_fields}"
    )
    assert len(source_fields) == len(set(source_fields)), "source_fields contains duplicates"

    # 4. MANDT note matches knowledge file
    assert "Client ID (Mandant)" in pack.mandt_note
    assert "Found in common cross-table fields:" not in pack.mandt_note

    # 5. Unresolved is empty for sheet 1
    assert len(pack.unresolved) == 0, (
        f"Expected 0 unresolved items for sheet 1, got {pack.unresolved}"
    )
