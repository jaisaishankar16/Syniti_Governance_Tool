"""Pydantic models for SQL and SAP agents, run records, and deps (Pydantic v2)."""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.agents.settings import LLMSettings
from app.guardrails.models import GuardrailReport
from app.knowledge.models import RetrievalHit
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.store import KnowledgeStore

T = TypeVar("T")


# ==============================================================================
# SQL Analysis Schemas (Strict-Schema Friendly)
# ==============================================================================


class SQLTableUsed(BaseModel):
    """Information on a table used in the analyzed query."""

    model_config = ConfigDict(extra="forbid")

    name: str
    role: str
    alias: str | None = None


class SQLJoinAnalysis(BaseModel):
    """Analysis of a join relationship in the query."""

    model_config = ConfigDict(extra="forbid")

    type: str
    left: str | None = None
    right: str | None = None
    condition: str | None = None
    cardinality_guess: str | None = None
    risk: str | None = None


class SQLFilter(BaseModel):
    """Analysis of a filter predicate in the query."""

    model_config = ConfigDict(extra="forbid")

    field: str
    operator: str
    value: str
    meaning: str


class SQLDerivation(BaseModel):
    """Analysis of a column derivation or rule projection (e.g. <expr> AS <FIELD>NEW)."""

    model_config = ConfigDict(extra="forbid")

    target: str
    kind: Literal["case", "constant", "lookup", "concat", "function"]
    logic: str
    inputs: list[str] = Field(default_factory=list)
    null_handling: str | None = None


class SQLKeywordExplained(BaseModel):
    """Explanation of a SQL construct / keyword present in this query."""

    model_config = ConfigDict(extra="forbid")

    keyword: str
    meaning_in_this_query: str


class KnowledgeSourceItem(BaseModel):
    """Reference to a knowledge source used during analysis."""

    model_config = ConfigDict(extra="forbid")

    item: str
    source: str
    verified: bool


class SQLAnalysis(BaseModel):
    """Comprehensive technical semantic analysis produced by SQLSemanticsAgent."""

    model_config = ConfigDict(extra="forbid")

    query_purpose: str
    statement_type: str
    view_name: str | None = None
    view_role: Literal["rule_view", "report_view", "export_view", "other"] = "other"
    tables_used: list[SQLTableUsed] = Field(default_factory=list)
    join_analysis: list[SQLJoinAnalysis] = Field(default_factory=list)
    filters: list[SQLFilter] = Field(default_factory=list)
    derivations: list[SQLDerivation] = Field(default_factory=list)
    aggregations: list[str] = Field(default_factory=list)
    window_functions: list[str] = Field(default_factory=list)
    ctes: list[str] = Field(default_factory=list)
    subqueries: list[str] = Field(default_factory=list)
    set_operations: list[str] = Field(default_factory=list)
    technical_summary: str
    performance_concerns: list[str] = Field(default_factory=list)
    correctness_concerns: list[str] = Field(default_factory=list)
    keywords_explained: list[SQLKeywordExplained] = Field(default_factory=list)
    unknown_items: list[str] = Field(default_factory=list)
    knowledge_sources: list[KnowledgeSourceItem] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


# ==============================================================================
# SAP Analysis Schemas (Strict-Schema Friendly)
# ==============================================================================


class SAPTableAnalysis(BaseModel):
    """Domain analysis of an individual table involved in the query."""

    model_config = ConfigDict(extra="forbid")

    name: str
    resolved_sap_table: str | None = None
    description: str
    module: str
    role: Literal[
        "sap_staging",
        "target_staging",
        "sap_native",
        "mapping_reference",
        "external_source",
        "custom",
        "unknown",
    ]
    role_in_query: str
    knowledge_source: Literal["prebuilt", "learned", "retrieved", "catalog", "none"]
    verified: bool


class SAPFieldAnalysis(BaseModel):
    """Domain analysis of a column / field referenced in the query."""

    model_config = ConfigDict(extra="forbid")

    table: str
    field: str
    description: str | None = None
    business_meaning: str | None = None
    is_custom: bool = False
    knowledge_source: Literal["prebuilt", "learned", "retrieved", "catalog", "none"]
    verified: bool = True


class SAPLearnedItem(BaseModel):
    """Knowledge learned and saved during this agent run."""

    model_config = ConfigDict(extra="forbid")

    table: str
    field: str | None = None
    action: str


class SAPAnalysis(BaseModel):
    """Comprehensive SAP domain analysis produced by SAPDomainAgent."""

    model_config = ConfigDict(extra="forbid")

    tables: list[SAPTableAnalysis] = Field(default_factory=list)
    fields: list[SAPFieldAnalysis] = Field(default_factory=list)
    sap_module: str
    business_concepts: list[str] = Field(default_factory=list)
    business_processes_involved: list[str] = Field(default_factory=list)
    client_handling: str
    key_relationships: list[str] = Field(default_factory=list)
    custom_field_notes: list[str] = Field(default_factory=list)
    mapping_tables: list[str] = Field(default_factory=list)
    unknown_items: list[str] = Field(default_factory=list)
    learned_this_run: list[SAPLearnedItem] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


# ==============================================================================
# Tool Results & Run Records
# ==============================================================================


class SqlKeywordLookupResult(BaseModel):
    """Result of looking up a SQL keyword."""

    model_config = ConfigDict(extra="forbid")

    term: str
    found: bool
    keyword: str | None = None
    category: str | None = None
    technical_meaning: str | None = None
    tsql_example: str | None = None
    performance_notes: str | None = None
    common_pitfalls: list[str] = Field(default_factory=list)
    source_chapter: str | None = None
    source: str | None = None
    verified: bool = False


class UsageInfo(BaseModel):
    """Token usage and request statistics for an agent run."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0


class ToolCallRecord(BaseModel):
    """Record of a tool call executed during an agent run."""

    model_config = ConfigDict(extra="forbid")

    name: str
    args_summary: str
    duration_ms: float
    ok: bool
    error: str | None = None


class AgentErrorDetails(BaseModel):
    """Details of an unrecoverable or terminal agent error."""

    model_config = ConfigDict(extra="forbid")

    type: str
    message: str
    retryable: bool
    error_code: str | None = None
    retry_after_s: int | None = None


class AgentOutcome(BaseModel, Generic[T]):
    """Standardized outcome for an individual agent execution."""

    model_config = ConfigDict(extra="forbid")

    agent_name: str
    status: Literal["ok", "failed", "timeout", "skipped", "blocked"]
    output: T | None = None
    error: AgentErrorDetails | None = None
    usage: UsageInfo = Field(default_factory=UsageInfo)
    latency_ms: float = 0.0
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    model: str
    attempts: int = 1
    prompt_version: str


class AgentsResult(BaseModel):
    """Combined parallel execution result for both SQL and SAP agents."""

    model_config = ConfigDict(extra="forbid")

    sql: AgentOutcome[SQLAnalysis]
    sap: AgentOutcome[SAPAnalysis]
    degraded: bool
    total_latency_ms: float
    request_id: str
    guardrails: GuardrailReport = Field(default_factory=GuardrailReport)


# ==============================================================================
# Context & Dependencies (SDK Run Context)
# ==============================================================================


class RunTrace(BaseModel):
    """In-memory trace capturing tool calls, chunks, and cached searches during an agent run."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    retrieved_chunks: dict[str, RetrievalHit] = Field(default_factory=dict)
    search_count: int = 0
    write_count: int = 0
    cached_search_results: dict[str, Any] = Field(default_factory=dict)
    learned_saves: list[dict[str, Any]] = Field(default_factory=list)


class AgentDeps(BaseModel):
    """Execution context and dependency container passed to Agents SDK."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    store: KnowledgeStore
    sap_retriever: SapRetriever
    sql_retriever: SqlDocRetriever
    settings: LLMSettings
    request_id: str
    run_trace: RunTrace = Field(default_factory=RunTrace)
