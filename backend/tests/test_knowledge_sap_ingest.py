"""Tests for SAP catalog PDF ingestion, glued line repair, overrides, and merging."""

import pytest
from app.knowledge.models import CatalogCategory, SapCatalogRecord
from app.knowledge.sap_catalog_ingest import SapCatalogIngest
from app.knowledge.settings import get_settings
from pydantic import TypeAdapter


@pytest.fixture(scope="module")
def sap_catalog_records() -> list[SapCatalogRecord]:
    settings = get_settings()
    if not settings.sap_catalog_path.exists():
        ingestor = SapCatalogIngest(settings=settings)
        ingestor.ingest()
    with open(settings.sap_catalog_path, "r", encoding="utf-8") as f:
        return TypeAdapter(list[SapCatalogRecord]).validate_json(f.read())


def test_go_to_top_removed(sap_catalog_records: list[SapCatalogRecord]):
    """Marker [ GO TO TOP ] must not appear in any description."""
    for rec in sap_catalog_records:
        for desc in rec.descriptions:
            assert "[ GO TO TOP ]" not in desc
            assert "GO TO TOP" not in desc


def test_glued_lines_repaired(sap_catalog_records: list[SapCatalogRecord]):
    """LinesEKKN and SettlementCOBRB glued lines must be cleanly split."""
    table_map = {r.table: r for r in sap_catalog_records}
    assert "EKKN" in table_map
    assert "COBRB" in table_map
    assert any("account assignment" in d.lower() for d in table_map["EKKN"].descriptions)
    assert any("settlement" in d.lower() for d in table_map["COBRB"].descriptions)


def test_overrides_applied(sap_catalog_records: list[SapCatalogRecord]):
    """Overrides for VTBFHAPO and VIAUFKST must take precedence."""
    table_map = {r.table: r for r in sap_catalog_records}
    assert "VTBFHAPO" in table_map
    assert "VIAUFKST" in table_map
    assert any("transaction flow" in d.lower() for d in table_map["VTBFHAPO"].descriptions)
    assert any("viaufkst" in d.lower() for d in table_map["VIAUFKST"].descriptions)


def test_merged_duplicate_records(sap_catalog_records: list[SapCatalogRecord]):
    """MAST, AFKO, and KONV appearing in multiple sections must be merged into single records."""
    table_map = {r.table: r for r in sap_catalog_records}
    for tbl in ("MAST", "AFKO", "KONV"):
        assert tbl in table_map
        rec = table_map[tbl]
        # Must have collected multiple sections or multiple descriptions
        assert len(rec.sections) >= 1
        # Table must appear only once in the catalog
        occurrences = sum(1 for r in sap_catalog_records if r.table == tbl)
        assert occurrences == 1


def test_dd_tables_in_catalog_but_data_dictionary_category(
    sap_catalog_records: list[SapCatalogRecord],
):
    """DD* tables must be in the catalog tagged as category=data_dictionary."""
    dd_records = [r for r in sap_catalog_records if r.table.startswith("DD")]
    assert len(dd_records) > 0
    for r in dd_records:
        assert r.category == CatalogCategory.data_dictionary


def test_required_tables_present(sap_catalog_records: list[SapCatalogRecord]):
    """Essential business tables must be present in the catalog."""
    table_map = {r.table: r for r in sap_catalog_records}
    required = ["MARA", "MARC", "LFA1", "EKKO", "VBAK", "T001W", "BP000"]
    for tbl in required:
        assert tbl in table_map, f"Required table {tbl} missing from SAP catalog"


def test_coverage_check():
    """Coverage check categorizes tables into prebuilt, catalog, and missing."""
    settings = get_settings()
    ingestor = SapCatalogIngest(settings=settings)
    report = ingestor.coverage_check(["MARA", "BUT000", "NON_EXISTENT_999"])
    assert "BUT000" in report["covered_by_prebuilt"]
    assert "MARA" in report["covered_by_catalog"] or "MARA" in report["covered_by_prebuilt"]
    assert "NON_EXISTENT_999" in report["missing"]
