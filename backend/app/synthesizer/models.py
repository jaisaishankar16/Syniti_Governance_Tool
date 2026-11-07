"""Pydantic v2 data models for the Phase 4 Synthesizer and Business Intelligence output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.guardrails.models import GuardrailReport

# ==============================================================================
# Governance & Risk Sub-Models
# ==============================================================================


class RiskSummary(BaseModel):
    """Structured assessment of risks identified in the analyzed query."""

    model_config = ConfigDict(extra="forbid")

    level: Literal["low", "medium", "high", "critical"] = Field(
        description="Overall risk severity level (low, medium, high, critical)"
    )
    items: list[str] = Field(
        default_factory=list,
        description="Specific identified risk items, flaws, or migration hazards",
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="Actionable remediation steps or architectural recommendations",
    )


class BusinessConcept(BaseModel):
    """The core business entity or concept governed by the rule."""

    model_config = ConfigDict(extra="forbid")

    term: str = Field(
        description="Standardized name of the business concept (e.g. 'Active Material Plant Status')"
    )
    field: str | None = Field(
        default=None,
        description="Specific column or attribute name associated with this concept, if applicable",
    )
    description: str = Field(
        description="Comprehensive definition of what this business concept represents"
    )


class IsNewFlags(BaseModel):
    """Flags indicating whether governance taxonomy items are proposed additions to the glossary."""

    model_config = ConfigDict(extra="forbid")

    subject_area: bool = Field(
        default=False,
        description="True if the subject_area was newly proposed rather than matched to existing glossary",
    )
    business_process: bool = Field(
        default=False,
        description="True if the business_process was newly proposed rather than matched to existing glossary",
    )
    business_concept: bool = Field(
        default=False,
        description="True if the business_concept was newly proposed rather than matched to existing glossary",
    )


class GovernanceOutput(BaseModel):
    """Official enterprise Data Governance catalog entry synthesized for this query."""

    model_config = ConfigDict(extra="forbid")

    subject_area: str = Field(
        description="Highest-level business category (e.g. 'Supply Chain', 'Finance', 'Material Master')"
    )
    business_process: str = Field(
        description="Operational business process where data quality is measured (e.g. 'Procure to Pay', 'Inventory Management')"
    )
    dataset: str = Field(description="Name of the governed dataset (e.g. 'Material Plant Data')")
    business_concept: BusinessConcept = Field(
        description="Core business entity or concept governed by this rule"
    )
    rule: str = Field(description="Concise, formal rule name or business requirement statement")
    specification: str = Field(
        description="Detailed functional rule specification stating conditions, required values, plants, and exclusions precisely"
    )
    enforcement: str = Field(
        description="Business impact or enforcement mechanism (e.g. 'Block migration of inactive plant records')"
    )
    implementation: str = Field(
        description="Technical description of how the rule is realized in SQL logic"
    )
    opportunity_definition: str = Field(
        description="Definition of what constitutes an opportunity record eligible for this rule"
    )
    error_definition: str = Field(
        description="Definition of what constitutes an error or non-compliant record"
    )
    opportunity_query: str = Field(
        description="Safe, read-only single-statement SELECT for all in-scope records of the driving table"
    )
    error_query: str = Field(
        description="Safe, read-only single-statement SELECT for records violating the rule (strict subset of opportunity)"
    )
    is_new_flags: IsNewFlags = Field(
        default_factory=IsNewFlags,
        description="Flags indicating whether taxonomy terms were newly proposed",
    )


# ==============================================================================
# Meta & Provenance Sub-Models
# ==============================================================================


class CacheDetail(BaseModel):
    """Detailed cache resolution status for individual agents."""

    model_config = ConfigDict(extra="forbid")

    sql_agent: Literal["hit", "miss", "similar_reused", "similar_hint", "skipped"] = "miss"
    sap_agent: Literal["hit", "miss", "similar_reused", "similar_hint", "skipped"] = "miss"


class PromptVersions(BaseModel):
    """Prompt version identifiers for each agent in the pipeline."""

    model_config = ConfigDict(extra="forbid")

    sql: str
    sap: str
    synthesizer: str


class TokenUsageBreakdown(BaseModel):
    """Token consumption across all pipeline stages."""

    model_config = ConfigDict(extra="forbid")

    sql: int = 0
    sap: int = 0
    synthesizer: int = 0
    embedding: int = 0
    total: int = 0


class ValidationSummary(BaseModel):
    """Summary of deterministic post-processing and guardrail validation checks."""

    model_config = ConfigDict(extra="forbid")

    opportunity_sql_ok: bool
    error_sql_ok: bool
    subset_ok: bool
    glossary_ok: bool
    matches_parser_baseline: bool = True


class BusinessIntelligenceMeta(BaseModel):
    """End-to-end provenance, performance, caching, and guardrail metadata."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    cache_status: Literal["hit_final", "partial", "miss", "refresh"]
    cache_detail: CacheDetail
    similarity_score: float | None = None
    model: str
    prompt_versions: PromptVersions
    agent_timings_ms: dict[str, float] = Field(default_factory=dict)
    stage_timings_ms: dict[str, float] = Field(default_factory=dict)
    token_usage: TokenUsageBreakdown = Field(default_factory=TokenUsageBreakdown)
    estimated_cost_usd: float | None = None
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    guardrails: GuardrailReport = Field(default_factory=GuardrailReport)
    validation: ValidationSummary
    ai_generated: bool = True
    parse_quality: str = "full"
    naming_violations: list[str] = Field(default_factory=list)
    cached_at: str | None = None


# ==============================================================================
# Root Output Models
# ==============================================================================


class SynthesizerRawOutput(BaseModel):
    """Raw structured output schema requested from the Synthesizer LLM agent."""

    model_config = ConfigDict(extra="forbid")

    business_meaning: str = Field(
        description="Clear, executive-ready explanation of what this query accomplishes from a business and migration perspective"
    )
    technical_summary: str = Field(
        description="Comprehensive technical summary synthesized from SQL structure, joins, filters, and SAP tables"
    )
    affected_entities: list[str] = Field(
        description="List of real SAP tables and migration staging entities affected by this rule"
    )
    sap_module: str = Field(description="Primary SAP module (e.g. 'MM', 'SD', 'FI', 'CO', 'PP')")
    query_type: str = Field(
        description="Classification of query purpose (e.g. 'Staging Rule View', 'Data Quality Validation', 'Reconciliation')"
    )
    risk_summary: RiskSummary = Field(
        description="Risk level, identified hazards, and recommended remediation actions"
    )
    final_learning: str | None = Field(
        default=None,
        description="Optional learned knowledge or observation derived during synthesis",
    )
    governance: GovernanceOutput = Field(
        description="Official enterprise Data Governance catalog entry"
    )


class BusinessIntelligence(BaseModel):
    """Complete, authoritative output of the MDM Syniti analysis engine."""

    model_config = ConfigDict(extra="forbid")

    business_meaning: str = Field(
        description="Executive business narrative explaining what this query accomplishes"
    )
    technical_summary: str = Field(
        description="Comprehensive technical summary synthesized from SQL semantics and SAP domain analysis"
    )
    affected_entities: list[str] = Field(
        description="List of real SAP tables and migration staging entities affected by this query"
    )
    sap_module: str = Field(description="Primary SAP functional module (e.g. MM, SD, FI, CO)")
    query_type: str = Field(
        description="Classification of query role (e.g. Staging Rule View, Data Quality Filter)"
    )
    risk_summary: RiskSummary = Field(description="Synthesized risk assessment and recommendations")
    final_learning: str | None = Field(
        default=None,
        description="Optional grounded learning insight from this analysis",
    )
    governance: GovernanceOutput = Field(
        description="Enterprise Data Governance rule catalog record"
    )
    meta: BusinessIntelligenceMeta = Field(
        description="Execution metadata, timings, caching, token usage, and guardrail reports"
    )
