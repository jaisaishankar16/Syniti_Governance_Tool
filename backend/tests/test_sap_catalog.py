"""Unit tests for SAP Table Catalog."""

from __future__ import annotations

import pytest
from app.parser.sap_catalog import SapTableCatalog


@pytest.fixture
def catalog() -> SapTableCatalog:
    return SapTableCatalog()


def test_known_sap_tables(catalog: SapTableCatalog) -> None:
    for table_name in ["MARA", "MARC", "BUT000", "BUT100", "LFA1", "WYT3", "T001W"]:
        assert catalog.is_known(table_name) is True
        desc = catalog.describe(table_name)
        assert desc is not None
        assert desc["name"] == table_name
        assert "module" in desc


def test_sap_table_resolution_order(catalog: SapTableCatalog) -> None:
    # 1. Naming engine's sap_table
    tbl, conf, is_custom = catalog.resolve_sap_table(
        raw_name="MM_MARC_ECC_EU_R1_MARC_S", naming_sap_table="MARC"
    )
    assert tbl == "MARC"
    assert conf == 1.0
    assert is_custom is False

    # 2. Exact catalog match
    tbl2, conf2, is_custom2 = catalog.resolve_sap_table(raw_name="MARA")
    assert tbl2 == "MARA"
    assert conf2 == 1.0
    assert is_custom2 is False

    # 3. Custom table Z/Y
    tbl3, conf3, is_custom3 = catalog.resolve_sap_table(raw_name="ZCUSTOM_TABLE")
    assert tbl3 == "ZCUSTOM_TABLE"
    assert conf3 > 0.0
    assert is_custom3 is True

    # 4. Skip CTE and Temp
    tbl_cte, _, _ = catalog.resolve_sap_table(raw_name="MY_CTE", is_cte=True)
    assert tbl_cte is None

    tbl_tmp, _, _ = catalog.resolve_sap_table(raw_name="#TEMP_TBL", is_temp=True)
    assert tbl_tmp is None


def test_role_determination(catalog: SapTableCatalog) -> None:
    assert (
        catalog.determine_role("MM_MARC_ECC_EU_R1_MARC_S", naming_kind="source_staging")
        == "sap_staging"
    )
    assert catalog.determine_role("VM_BUT000_T", naming_kind="target_staging") == "sap_staging"
    assert (
        catalog.determine_role("MM_WERKS_XREF", naming_kind="mapping_reference")
        == "mapping_reference"
    )
    assert (
        catalog.determine_role("MM_MATERIAL_SIEVO_DATADUMP", naming_kind="external_source")
        == "external_source"
    )
    assert catalog.determine_role("MARA", sap_table="MARA") == "sap_native"
    assert catalog.determine_role("#TEMP", is_temp=True) == "temp"
    assert catalog.determine_role("CTE_NAME", is_cte=True) == "cte"
