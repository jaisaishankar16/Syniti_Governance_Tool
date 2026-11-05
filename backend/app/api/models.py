"""Pydantic v2 schemas and models for the API layer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Maximum allowed SQL character length (consistent with GuardrailSettings)
MAX_SQL_CHARS = 50_000


class AnalyzeRequest(BaseModel):
    """Request payload for executing SQL analysis."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "sql": "SELECT MARC.MATNR, MARA.MTART FROM dbo.MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN dbo.MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1",
                "force_refresh": False,
            }
        },
    )

    sql: str = Field(
        ...,
        min_length=1,
        max_length=MAX_SQL_CHARS,
        description="Raw T-SQL or ANSI SQL view definition or query to analyze",
    )
    force_refresh: bool = Field(
        default=False,
        description="When true, bypasses L1/L2 cache reads and executes fresh LLM analysis",
    )


class Principal(BaseModel):
    """Authenticated user principal identity for audit and rate-limiting."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique subject or user identifier")
    display_name: str = Field(description="Human-readable name")
    email: str = Field(description="User email address")
    roles: list[str] = Field(default_factory=list, description="Assigned authorization roles")
    initials: str = Field(default="AU", description="Two-letter initials for user avatar display")


class Project(BaseModel):
    """Enterprise MDM Syniti migration project placeholder metadata."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique project identifier")
    name: str = Field(description="Project title")
    description: str = Field(description="Summary of project scope and migration waves")
    status: Literal["active", "archived"] = Field(description="Lifecycle status of project")
    release_count: int = Field(description="Number of releases configured for project")
    milestone_count: int = Field(description="Number of governance milestones")


class ProjectCreateRequest(BaseModel):
    """Request payload for creating a migration project."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=80, description="Project name")
    description: str | None = Field(
        default=None, max_length=300, description="Optional project description"
    )


class MetadataRefreshRequest(BaseModel):
    """Request payload for initiating SKP metadata refresh."""

    model_config = ConfigDict(extra="forbid")

    skp_url: str = Field(
        ..., min_length=1, max_length=2048, description="Target SKP service endpoint URL"
    )


class ActivityItem(BaseModel):
    """Audit and activity timeline event."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique event identifier")
    request_id: str | None = Field(default=None, description="Request ID associated with execution")
    status: Literal["complete", "degraded", "failed"] = Field(description="Job or analysis status")
    run_type: str = Field(description="Classification of execution (e.g. Trial Execution)")
    table_name: str = Field(description="Target table or view name")
    subtitle: str = Field(description="Context or business area subtitle")
    project_id: str | None = Field(default=None, description="Parent project identifier")
    cache_status: str | None = Field(
        default=None, description="Cache outcome (e.g. miss, hit_final)"
    )
    duration_ms: float | None = Field(default=None, description="Duration in milliseconds")
    finished_at: str = Field(description="ISO-8601 timestamp of completion")


class ExampleQueryItem(BaseModel):
    """Pre-curated workbook query item for UI convenience."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Identifier or sheet name (e.g. Sheet1)")
    title: str = Field(description="Descriptive view title derived from SQL structure")
    sql: str = Field(description="Input SQL statement text only")


class LLMBudgetStatus(BaseModel):
    """Current consumption and daily token budget limit."""

    model_config = ConfigDict(extra="forbid")

    used_tokens: int = Field(description="Cumulative tokens used today")
    daily_limit: int = Field(description="Configured daily token budget limit")


class LLMStatusResponse(BaseModel):
    """Health and configuration status of the underlying LLM subsystem."""

    model_config = ConfigDict(extra="forbid")

    configured: bool = Field(description="Whether a valid, non-placeholder API key is configured")
    model: str = Field(description="Active OpenAI model identifier")
    api_mode: str = Field(description="SDK connection mode (e.g. responses)")
    base_url_set: bool = Field(description="Whether a custom OPENAI_BASE_URL is active")
    circuit_state: str = Field(description="Circuit breaker status (CLOSED, OPEN, HALF_OPEN)")
    budget: LLMBudgetStatus = Field(description="Current token consumption against budget")
    last_error_code: str | None = Field(
        default=None, description="Last provider error code if within active window"
    )
    last_error_at: str | None = Field(
        default=None, description="ISO timestamp of last provider error"
    )


class HealthResponse(BaseModel):
    """Liveness probe response."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="ok", description="Process health indicator")
    timestamp: str = Field(description="Current UTC timestamp")


class ReadyResponse(BaseModel):
    """Readiness probe response detailing individual subsystem checks."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"] = Field(description="Overall deployment readiness")
    checks: dict[str, bool] = Field(
        description="Detailed subsystem check results (indexes_loaded, cache_healthy, llm_configured)"
    )


class AdminPingResponse(BaseModel):
    """Result of live 1-token test probe against LLM provider."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "401", "429", "network_error"] = Field(description="Probe status outcome")
    latency_ms: float = Field(description="Measured round-trip latency in milliseconds")
    message: str = Field(description="Sanitized outcome description")


class CacheClearResponse(BaseModel):
    """Response payload for cache deletion operations."""

    model_config = ConfigDict(extra="forbid")

    cleared: bool = Field(description="Whether cache was cleared successfully")
    kind: str = Field(description="Target cache tier (all, l1, l2, l3)")


class LlmSettingsView(BaseModel):
    """Read-only diagnostics and configuration view of the LLM pipeline."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(description="Active LLM model name")
    api_mode: str = Field(description="OpenAI SDK connection mode")
    structured_output_mode: str = Field(description="Structured output enforcement mode")
    temperature: float = Field(description="Sampling temperature")
    max_turns: int = Field(description="Maximum reasoning turns")
    timeout_s: float = Field(description="API timeout in seconds")
    max_retries: int = Field(description="Maximum upstream retries")
    provider: str = Field(description="Target provider protocol")
    base_url: str = Field(description="Sanitized scheme+host of provider base URL or 'default'")
    api_key_configured: bool = Field(description="Whether a valid, non-placeholder API key is set")
    learning_enabled: bool = Field(
        description="Whether dynamic knowledge learning write-back is enabled"
    )
    tracing_enabled: bool = Field(description="Whether telemetry tracing is active")
    token_budgets: dict[str, Any] = Field(
        description="Request and daily token allocation and usage"
    )
    circuit_state: str = Field(description="Circuit breaker state (CLOSED, OPEN, HALF_OPEN)")
    last_error_code: str | None = Field(default=None, description="Last detected error code")
    last_error_at: str | None = Field(default=None, description="ISO timestamp of last failure")
    prompt_versions: dict[str, str] = Field(description="Active hash versions of system prompts")
    embedding_model: str = Field(description="Active sentence-transformer embedding model")
    knowledge_index_status: dict[str, Any] = Field(description="FAISS index status for SAP and SQL")
    cache_backend: dict[str, Any] = Field(description="Multi-tier cache parameters and TTLs")
    guardrail_modes: dict[str, Any] = Field(description="Active guardrail configuration")
    pricing_configured: bool = Field(description="Whether a token pricing table is configured")
