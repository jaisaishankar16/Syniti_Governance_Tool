"""Pydantic models and typed errors for SQL Parser and Syniti Naming Engine."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ParseError(Exception):
    """Typed error for unrecoverable or invalid SQL parsing."""

    def __init__(
        self,
        message: str,
        line: int | None = None,
        column: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column
        self.details = details or {}

    def __str__(self) -> str:
        loc = []
        if self.line is not None:
            loc.append(f"line {self.line}")
        if self.column is not None:
            loc.append(f"col {self.column}")
        location_str = f" ({', '.join(loc)})" if loc else ""
        return f"ParseError{location_str}: {self.message}"


class NamingViolation(BaseModel):
    """Violation reported against naming conventions."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["error", "warning", "info"] = "warning"
    message: str


class NamingResult(BaseModel):
    """Result of evaluating an identifier with the Syniti naming convention engine."""

    model_config = ConfigDict(extra="forbid")

    object_kind: Literal[
        "source_staging",
        "target_staging",
        "rule_view",
        "report_view",
        "export_view",
        "mapping_reference",
        "external_source",
        "working_db",
        "unknown",
    ] = "unknown"
    prefix: str | None = None
    dataset: str | None = None
    sap_table: str | None = None
    zsource: str | None = None
    source_table: str | None = None
    action: str | None = None
    field: str | None = None
    description: str | None = None
    working_table: str | None = None
    compliant: bool = True
    violations: list[NamingViolation] = Field(default_factory=list)
    confidence: float = 1.0


class TableInfo(BaseModel):
    """Information on a table referenced in a query."""

    model_config = ConfigDict(populate_by_name=True)

    raw_name: str
    alias: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    database: str | None = None
    role: Literal[
        "sap_staging",
        "sap_native",
        "mapping_reference",
        "external_source",
        "cte",
        "temp",
        "unknown",
    ] = "unknown"
    sap_table: str | None = None
    naming: NamingResult | None = None


class ColumnInfo(BaseModel):
    """Information on a column referenced or projected in a query."""

    model_config = ConfigDict(extra="forbid")

    table: str | None = None
    column: str
    usage: Literal[
        "select",
        "where",
        "join",
        "group",
        "order",
        "aggregate",
        "derived",
    ] = "select"
    is_custom: bool = False


class JoinInfo(BaseModel):
    """Information on a JOIN between tables."""

    model_config = ConfigDict(extra="forbid")

    type: str = "INNER"
    left_table: str | None = None
    right_table: str | None = None
    condition: str | None = None
    left_field: str | None = None
    right_field: str | None = None


class CTEInfo(BaseModel):
    """Common Table Expression specification."""

    model_config = ConfigDict(extra="forbid")

    name: str
    columns: list[str] = Field(default_factory=list)
    recursive: bool = False


class UpdateTarget(BaseModel):
    """Target column updated in a Syniti rule view (expression AS <FIELD>NEW)."""

    model_config = ConfigDict(extra="forbid")

    field: str
    new_alias: str
    expression: str
    expression_kind: Literal["case", "constant", "lookup", "concat", "function"]
    source_fields: list[str] = Field(default_factory=list)
    rule_kind: Literal["source", "target"]


class RiskFlag(BaseModel):
    """Risk flag indicating potential performance or logic issues."""

    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Literal["critical", "high", "medium", "low", "info", "warning"]
    message: str


class Complexity(BaseModel):
    """Query complexity evaluation."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(default=1, ge=1, le=10)
    level: Literal["low", "medium", "high", "critical"] = "low"
    factors: list[str] = Field(default_factory=list)


class ParsedQuery(BaseModel):
    """Normalized and parsed representation of a T-SQL query."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    statement_type: str = "SELECT"
    view_name: str | None = None
    view_schema: str | None = None
    view_naming: NamingResult | None = None
    driving_table: str | None = None
    tables: list[TableInfo] = Field(default_factory=list)
    columns: list[ColumnInfo] = Field(default_factory=list)
    joins: list[JoinInfo] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    scope_filters: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    order_by: list[str] = Field(default_factory=list)
    aggregations: list[str] = Field(default_factory=list)
    window_functions: list[str] = Field(default_factory=list)
    ctes: list[CTEInfo] = Field(default_factory=list)
    subqueries: list[str] = Field(default_factory=list)
    set_operations: list[str] = Field(default_factory=list)
    top_limit: str | None = None
    update_targets: list[UpdateTarget] = Field(default_factory=list)
    comments: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    normalized_sql: str
    query_hash: str
    wrapper_hash: str | None = None
    parse_quality: Literal["full", "partial", "fallback"] = "full"
    warnings: list[str] = Field(default_factory=list)
    ast_json: dict[str, Any] | None = None
    sql_part: dict[str, Any] = Field(default_factory=dict)
    sap_part: dict[str, Any] = Field(default_factory=dict)
    complexity: Complexity = Field(default_factory=Complexity)
    risk_flags: list[RiskFlag] = Field(default_factory=list)
