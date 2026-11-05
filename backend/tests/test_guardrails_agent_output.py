"""Unit tests for agent output guardrails: parser ground truth reconciliation and sanitization."""

import pytest
from app.agents.knowledge_pack import (
    SapKnowledgePack,
    SapPackResolvedItem,
    SapPackUnresolvedItem,
    SqlKnowledgePack,
)
from app.agents.models import (
    SAPAnalysis,
    SAPFieldAnalysis,
    SAPTableAnalysis,
    SQLAnalysis,
    SQLTableUsed,
)
from app.guardrails.agent_output import guardrail_sap_output, guardrail_sql_output
from app.guardrails.settings import GuardrailSettings
from app.parser.models import ParsedQuery
from app.parser.sql_parser import SqlParser


@pytest.fixture
def dummy_parsed() -> ParsedQuery:
    parser = SqlParser()
    return parser.parse("SELECT m.MATNR, m.WERKS FROM MM_MARC_ECC_EU_R1_MARC_S AS m")


@pytest.fixture
def dummy_sap_pack() -> SapKnowledgePack:
    return SapKnowledgePack(
        resolved=[
            SapPackResolvedItem(
                table="MARC",
                field="MATNR",
                level="field",
                description="Material Number",
                source="prebuilt",
                verified=True,
            )
        ],
        unresolved=[
            SapPackUnresolvedItem(
                table="MARC",
                field="ZUNKNOWN",
                reason="Not in catalog or prebuilt",
            )
        ],
    )


@pytest.fixture
def dummy_sql_pack() -> SqlKnowledgePack:
    return SqlKnowledgePack()


@pytest.fixture
def settings() -> GuardrailSettings:
    return GuardrailSettings()


def test_sap_output_hallucinated_table_and_field_dropped(
    dummy_parsed: ParsedQuery,
    dummy_sap_pack: SapKnowledgePack,
    settings: GuardrailSettings,
) -> None:
    output = SAPAnalysis(
        tables=[
            SAPTableAnalysis(
                name="MM_MARC_ECC_EU_R1_MARC_S",
                resolved_sap_table="MARC",
                description="Plant data",
                module="MM",
                role="custom",  # Wrong role
                role_in_query="Plant staging",
                knowledge_source="prebuilt",
                verified=True,
            ),
            SAPTableAnalysis(
                name="MM_FAKE_ECC_EU_R1_FAKE_S",
                resolved_sap_table="FAKE",
                description="Invented",
                module="MM",
                role="sap_staging",  # Hallucinated table
                role_in_query="Fake staging",
                knowledge_source="none",
                verified=False,
            ),
        ],
        fields=[
            SAPFieldAnalysis(
                table="MARC",
                field="MATNR",
                description="Material Number",
                knowledge_source="prebuilt",
                verified=True,
            ),
            SAPFieldAnalysis(
                table="MARC",
                field="FAKE_FIELD",
                description="Invented Field",
                knowledge_source="none",
                verified=False,
            ),
        ],
        sap_module="MM",
        client_handling="Standard <script>alert('pwn')</script>",
        confidence=1.0,
    )

    reconciled, _events, blocked = guardrail_sap_output(
        output,
        dummy_parsed,
        dummy_sap_pack,
        canary_token="CANARY_TEST_1234",
        settings=settings,
    )

    assert not blocked
    # Hallucinated table dropped
    assert len(reconciled.tables) == 1
    assert reconciled.tables[0].name == "MM_MARC_ECC_EU_R1_MARC_S"
    # Role corrected to parser ground-truth
    assert reconciled.tables[0].role == "sap_staging"

    # Invented field dropped
    assert len(reconciled.fields) == 1
    assert reconciled.fields[0].field == "MATNR"

    # Script tag stripped
    assert "<script>" not in reconciled.client_handling
    assert "Standard" in reconciled.client_handling

    # Missing unresolved pack items added to unknown_items
    assert any("ZUNKNOWN" in item for item in reconciled.unknown_items)

    # Confidence capped due to unresolved items
    assert reconciled.confidence < 1.0


def test_sap_output_canary_leak_blocked(
    dummy_parsed: ParsedQuery,
    dummy_sap_pack: SapKnowledgePack,
    settings: GuardrailSettings,
) -> None:
    canary = "CANARY_SECRET_LEAK_999"
    output = SAPAnalysis(
        tables=[
            SAPTableAnalysis(
                name="MM_MARC_ECC_EU_R1_MARC_S",
                resolved_sap_table="MARC",
                description="Plant data",
                module="MM",
                role="sap_staging",
                role_in_query="Plant staging",
                knowledge_source="prebuilt",
                verified=True,
            )
        ],
        fields=[],
        sap_module="MM",
        client_handling=f"Here is your canary: {canary}",
        confidence=1.0,
    )

    _, events, blocked = guardrail_sap_output(
        output,
        dummy_parsed,
        dummy_sap_pack,
        canary_token=canary,
        settings=settings,
    )

    assert blocked is True
    assert any(e.name == "canary_token_leaked" and e.action == "block" for e in events)


def test_sql_output_reconciliation(
    dummy_parsed: ParsedQuery,
    dummy_sql_pack: SqlKnowledgePack,
    settings: GuardrailSettings,
) -> None:
    output = SQLAnalysis(
        query_purpose="Query test with url https://malicious.com/hack",
        statement_type="CREATE_VIEW",  # Differs from parser 'SELECT'
        view_name="FAKE_VIEW",  # Differs from parser None
        tables_used=[
            SQLTableUsed(name="MM_MARC_ECC_EU_R1_MARC_S", role="staging"),
            SQLTableUsed(name="INVENTED_TABLE", role="staging"),
        ],
        technical_summary="<b>Summary</b> with HTML tags",
    )

    reconciled, _events, blocked = guardrail_sql_output(
        output,
        dummy_parsed,
        dummy_sql_pack,
        canary_token="CANARY_SQL_1234",
        settings=settings,
    )

    assert not blocked
    # Parser wins on statement_type and view_name
    assert reconciled.statement_type == "SELECT"
    assert reconciled.view_name is None

    # Hallucinated table dropped
    assert len(reconciled.tables_used) == 1
    assert reconciled.tables_used[0].name == "MM_MARC_ECC_EU_R1_MARC_S"

    # HTML tags stripped
    assert "<b>" not in reconciled.technical_summary
    assert "Summary" in reconciled.technical_summary

    # External URL redacted
    assert "https://malicious.com" not in reconciled.query_purpose
    assert "[URL_REDACTED]" in reconciled.query_purpose
