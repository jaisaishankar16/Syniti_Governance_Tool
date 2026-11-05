"""Unit tests for Syniti Migrate Naming Convention Engine."""

from __future__ import annotations

import pytest
from app.parser.naming import SynitiNamingEngine


@pytest.fixture
def naming_engine() -> SynitiNamingEngine:
    return SynitiNamingEngine()


def test_target_staging_table(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("VM_BUT000_T")
    assert res.object_kind == "target_staging"
    assert res.prefix == "VM"
    assert res.sap_table == "BUT000"
    assert res.compliant is True
    assert len(res.violations) == 0


def test_source_staging_known_zsource(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("MM_MARC_ECC_EU_R1_MARC_S")
    assert res.object_kind == "source_staging"
    assert res.prefix == "MM"
    assert res.sap_table == "MARC"
    assert res.zsource == "ECC_EU_R1"
    assert res.source_table == "MARC"
    assert res.compliant is True
    assert res.confidence == 1.0


def test_source_staging_heuristic_zsource(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("CM_KNA1_LEGACY_CRM_CUSTOMERS_S")
    assert res.object_kind == "source_staging"
    assert res.prefix == "CM"
    assert res.sap_table == "KNA1"
    assert res.source_table == "CUSTOMERS"
    assert res.zsource == "LEGACY_CRM"
    assert res.confidence < 1.0


def test_rule_view_standard(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("MM_MARC_ECC_EU_R1_MARC_S_UPD_ZSTRGR", is_view=True)
    assert res.object_kind == "rule_view"
    assert res.prefix == "MM"
    assert res.sap_table == "MARC"
    assert res.zsource == "ECC_EU_R1"
    assert res.action == "UPD"
    assert res.field == "ZSTRGR"
    assert res.compliant is True


def test_rule_view_with_description(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("MM_MARC_ECC_EU_R1_MARC_S_UPD_ZSSQSS_QM999999", is_view=True)
    assert res.object_kind == "rule_view"
    assert res.action == "UPD"
    assert res.field == "ZSSQSS"
    assert res.description == "QM999999"
    assert res.compliant is True


def test_action_order_violation(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("VM_BUT100_ECC_EU_R1_LFA1_S_FLVN00_UPD_ZACTIVE", is_view=True)
    assert res.object_kind == "rule_view"
    assert res.action == "UPD"
    assert res.field == "ZACTIVE"
    assert res.compliant is False
    codes = [v.code for v in res.violations]
    assert "ACTION_ORDER" in codes


def test_lowercase_violation(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("VM_BUT000_ECC_EU_R1_LFA1_S_UPD_zACTIVE", is_view=True)
    assert res.object_kind == "rule_view"
    assert res.compliant is False
    codes = [v.code for v in res.violations]
    assert "LOWERCASE" in codes


def test_missing_suffix_violation(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("MM_MARC_INVALID")
    assert res.compliant is False
    codes = [v.code for v in res.violations]
    assert "MISSING_SUFFIX" in codes


def test_unknown_prefix_violation(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("XX_MARC_T")
    assert res.compliant is False
    codes = [v.code for v in res.violations]
    assert "UNKNOWN_PREFIX" in codes


def test_report_view(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("MM_MARA_ECC_EU_R1_MARA_S_RPT_STRAS_ISNULL", is_view=True)
    assert res.object_kind == "report_view"
    assert res.field == "STRAS"
    assert res.description == "ISNULL"
    assert res.compliant is True


def test_export_view(naming_engine: SynitiNamingEngine) -> None:
    res = naming_engine.parse_name("VM_LFA1_T_EXP_VENDOR_MASTER", is_view=True)
    assert res.object_kind == "export_view"
    assert res.description == "VENDOR_MASTER"
    assert res.compliant is True


def test_reference_keywords(naming_engine: SynitiNamingEngine) -> None:
    res1 = naming_engine.parse_name("MM_WERKS_XREF")
    assert res1.object_kind == "mapping_reference"
    assert res1.compliant is True

    res2 = naming_engine.parse_name("MM_MATKL_L_XREF")
    assert res2.object_kind == "mapping_reference"
    assert res2.compliant is True

    res3 = naming_engine.parse_name("MM_MARA_MATKL_SIEVO")
    assert res3.object_kind == "mapping_reference"
    assert res3.compliant is True

    res4 = naming_engine.parse_name("MM_MATERIAL_SIEVO_DATADUMP")
    assert res4.object_kind == "external_source"
    assert res4.compliant is True


def test_working_database_naming(naming_engine: SynitiNamingEngine) -> None:
    res1 = naming_engine.parse_name("WRKPTP")
    assert res1.object_kind == "working_db"
    assert res1.dataset == "PTP"

    res2 = naming_engine.parse_name("WRKPTP01D")
    assert res2.object_kind == "working_db"
    assert res2.dataset == "PTP"


def test_bracketed_column_space_info_severity(naming_engine: SynitiNamingEngine) -> None:
    violations = naming_engine.check_universal_rules("[ESPHORA Plant]", is_column=True)
    assert len(violations) > 0
    assert any(v.severity == "info" and v.code == "SPECIAL_CHAR_OR_SPACE" for v in violations)
