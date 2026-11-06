"""Fake Models and mock runners for deterministic offline testing and unit test execution."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agents import Model, ModelResponse, Usage
from agents.models.chatcmpl_converter import Converter
from app.agents.models import (
    SAPAnalysis,
    SAPFieldAnalysis,
    SAPTableAnalysis,
    SQLAnalysis,
    SQLDerivation,
    SQLFilter,
    SQLJoinAnalysis,
    SQLKeywordExplained,
    SQLTableUsed,
)
from app.synthesizer.models import (
    BusinessConcept,
    GovernanceOutput,
    IsNewFlags,
    RiskSummary,
    SynthesizerRawOutput,
)
from openai.types.chat import ChatCompletionMessage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agents import AgentOutputSchemaBase, Handoff, ModelSettings, ModelTracing, Tool
    from agents.items import TResponseInputItem, TResponseStreamEvent
    from openai.types.responses.response_prompt_param import ResponsePromptParam


# ==============================================================================
# Canonical Pre-canned Agent Outputs for Unit Testing
# ==============================================================================

CANNED_SQL_ANALYSIS = SQLAnalysis(
    query_purpose="Extract material and plant attributes with active flag filtering and target column derivation",
    statement_type="SELECT",
    view_name="MM_MARC_ECC_EU_R1_MARC_S",
    view_role="rule_view",
    tables_used=[
        SQLTableUsed(name="MM_MARC_ECC_EU_R1_MARC_S", role="sap_staging", alias="m"),
        SQLTableUsed(name="MM_MARA_ECC_EU_R1_MARA_S", role="sap_staging", alias="a"),
    ],
    join_analysis=[
        SQLJoinAnalysis(
            type="INNER",
            left="m",
            right="a",
            condition="m.MATNR = a.MATNR",
            cardinality_guess="many-to-one",
            risk="None detected; standard material master join",
        )
    ],
    filters=[
        SQLFilter(field="MANDT", operator="=", value="'100'", meaning="Client isolation filter"),
        SQLFilter(field="ZACTIVE", operator="=", value="1", meaning="Staging active record filter"),
    ],
    derivations=[
        SQLDerivation(
            target="FLAGNEW",
            kind="case",
            logic="CASE WHEN m.WERKS = '1000' THEN 1 ELSE 0 END",
            inputs=["m.WERKS"],
            null_handling="Defaults to 0 on NULL or non-match",
        )
    ],
    aggregations=[],
    window_functions=[],
    ctes=[],
    subqueries=[],
    set_operations=[],
    technical_summary="Inner join between MARC and MARA staging tables with client and active record filtering.",
    performance_concerns=[],
    correctness_concerns=[],
    keywords_explained=[
        SQLKeywordExplained(
            keyword="INNER JOIN", meaning_in_this_query="Combines plant and general material rows"
        ),
        SQLKeywordExplained(
            keyword="CASE", meaning_in_this_query="Evaluates plant code to derive target flag"
        ),
    ],
    unknown_items=[],
    knowledge_sources=[],
    confidence=1.0,
)

CANNED_SAP_ANALYSIS = SAPAnalysis(
    tables=[
        SAPTableAnalysis(
            name="MM_MARC_ECC_EU_R1_MARC_S",
            resolved_sap_table="MARC",
            description="Plant Data for Material",
            module="MM",
            role="sap_staging",
            role_in_query="Driving plant-level staging table",
            knowledge_source="prebuilt",
            verified=True,
        ),
        SAPTableAnalysis(
            name="MM_MARA_ECC_EU_R1_MARA_S",
            resolved_sap_table="MARA",
            description="General Material Data",
            module="MM",
            role="sap_staging",
            role_in_query="Material master client-level header attributes",
            knowledge_source="prebuilt",
            verified=True,
        ),
    ],
    fields=[
        SAPFieldAnalysis(
            table="MARC",
            field="MATNR",
            description="Material Number",
            business_meaning="Primary material master key identifier",
            is_custom=False,
            knowledge_source="prebuilt",
            verified=True,
        ),
        SAPFieldAnalysis(
            table="MARC",
            field="WERKS",
            description="Plant",
            business_meaning="Manufacturing or logistics facility code",
            is_custom=False,
            knowledge_source="prebuilt",
            verified=True,
        ),
        SAPFieldAnalysis(
            table="MARC",
            field="ZACTIVE",
            description="Active Flag",
            business_meaning="Syniti Migrate migration staging active flag",
            is_custom=True,
            knowledge_source="prebuilt",
            verified=True,
        ),
    ],
    sap_module="MM",
    business_concepts=["Material Master", "Plant Data", "Staging Isolation"],
    business_processes_involved=["Procure to Pay", "Inventory Management"],
    client_handling="Standard MANDT client isolation filter applied in WHERE predicate.",
    key_relationships=["MARC to MARA on MATNR (Plant to General Material)"],
    custom_field_notes=["ZACTIVE used to filter active migration records"],
    mapping_tables=[],
    unknown_items=[],
    learned_this_run=[],
    confidence=1.0,
)

CANNED_SYNTHESIZER_OUTPUT = SynthesizerRawOutput(
    business_meaning="Material Master active staging view deriving ZSTRGR planning flag",
    technical_summary="Inner join between MARC and MARA on MATNR deriving ZSTRGRNEW with CASE expression",
    affected_entities=["MM_MARC_ECC_EU_R1_MARC_S", "MM_MARA_ECC_EU_R1_MARA_S"],
    sap_module="MM",
    query_type="Staging Rule View",
    risk_summary=RiskSummary(
        level="low",
        items=["No structural defects identified in query join or predicates"],
        recommendations=["Ensure downstream consumers consume derived ZSTRGRNEW field"],
    ),
    final_learning=None,
    governance=GovernanceOutput(
        subject_area="Procure to Pay / Material Management",
        business_process="Material Master – Plant Data Standardization",
        dataset="MARC (Plant Data) + MARA (General Material Data)",
        business_concept=BusinessConcept(
            term="Storage/Strategy Group",
            field="ZSTRGR",
            description="Strategy group defining material handling and planning parameters",
        ),
        rule="Storage/Strategy Group derived from plant code and material type",
        specification="For active materials (ZACTIVE = 1), ZSTRGR should be 40 when plant matches criteria",
        enforcement="Validate derived ZSTRGRNEW matches expected business rule value",
        implementation="SQL CASE expression deriving ZSTRGRNEW using substring and MTART",
        opportunity_definition="All active material-plant records where ZACTIVE = 1",
        error_definition="Records where derived ZSTRGRNEW is NULL or does not match expected value",
        opportunity_query="SELECT * FROM MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1",
        error_query="SELECT MARC.MATNR FROM MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1 AND MARC.WERKS IS NULL",
        is_new_flags=IsNewFlags(),
    ),
)


# ==============================================================================
# FakeModel Implementation of agents.Model
# ==============================================================================


class FakeModel(Model):
    """Fake model implementing agents.Model for unit testing without network or OpenAI credentials."""

    def __init__(
        self,
        canned_response: str | None = None,
        responses: list[str] | None = None,
        sleep_s: float = 0.0,
        error_to_raise: Exception | None = None,
        input_tokens: int = 50,
        output_tokens: int = 25,
    ) -> None:
        self.canned_response = canned_response
        self.responses = list(responses) if responses else []
        self.sleep_s = sleep_s
        self.error_to_raise = error_to_raise
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.call_history: list[dict[str, Any]] = []

    def __str__(self) -> str:
        return "FakeModel"

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: ResponsePromptParam | None = None,
    ) -> ModelResponse:
        t0 = asyncio.get_event_loop().time()
        if self.sleep_s > 0:
            await asyncio.sleep(self.sleep_s)
        t1 = asyncio.get_event_loop().time()

        self.call_history.append(
            {
                "system_instructions": system_instructions,
                "input": input,
                "tools": [t.name for t in tools],
                "start_time": t0,
                "end_time": t1,
            }
        )

        if self.error_to_raise is not None:
            raise self.error_to_raise

        # Determine response text
        if self.responses:
            resp_text = self.responses.pop(0)
        elif self.canned_response is not None:
            resp_text = self.canned_response
        else:
            # Auto-detect from system_instructions, output_schema, or input
            sys_str = system_instructions or ""
            schema_str = str(output_schema) if output_schema else ""
            if "SQLSemanticsAgent" in sys_str or "SQLAnalysis" in schema_str:
                resp_text = CANNED_SQL_ANALYSIS.model_dump_json()
            elif "SAPDomainAgent" in sys_str or "SAPAnalysis" in schema_str:
                resp_text = CANNED_SAP_ANALYSIS.model_dump_json()
            elif "Synthesizer" in sys_str or "SynthesizerRawOutput" in schema_str:
                resp_text = CANNED_SYNTHESIZER_OUTPUT.model_dump_json()
            else:
                resp_text = CANNED_SQL_ANALYSIS.model_dump_json()

        msg = ChatCompletionMessage(role="assistant", content=resp_text)
        items = Converter.message_to_output_items(msg)
        usage = Usage(
            requests=1,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.input_tokens + self.output_tokens,
        )

        return ModelResponse(
            output=items,
            usage=usage,
            response_id="fake_resp_id",
        )

    def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: ResponsePromptParam | None = None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        raise NotImplementedError("Streaming not implemented in FakeModel")
