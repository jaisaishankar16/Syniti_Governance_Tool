"""Pydantic models and schemas for knowledge layer (Pydantic v2)."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ==============================================================================
# Fixed Vocabularies (Enums)
# ==============================================================================


class StaleIndexError(Exception):
    """Raised when an index manifest is missing, out-of-date, or mismatched."""


class KnowledgeSource(str, Enum):
    prebuilt = "prebuilt"
    learned = "learned"
    retrieved = "retrieved"
    catalog = "catalog"


class ConfidenceLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    verified = "verified"


class CatalogCategory(str, Enum):
    business = "business"
    system = "system"
    data_dictionary = "data_dictionary"
    report = "report"


class MatchType(str, Enum):
    exact = "exact"
    semantic = "semantic"


class SqlDialect(str, Enum):
    tsql = "tsql"
    oracle = "oracle"
    mysql = "mysql"
    postgres = "postgres"
    ansi = "ansi"
    unknown = "unknown"


# ==============================================================================
# Core Domain Entities
# ==============================================================================


class SapField(BaseModel):
    """Schema for a SAP table column / field definition."""

    model_config = ConfigDict(extra="forbid")

    field: str
    description: str
    data_element: str | None = None
    data_type: str | None = None
    length: int | None = None
    key: bool = False
    business_meaning: str | None = None
    is_custom: bool = False
    verified: bool = False


class SapTableJoin(BaseModel):
    """Common join pattern between SAP tables."""

    model_config = ConfigDict(extra="forbid")

    to_table: str
    on: str


class SapTable(BaseModel):
    """Rich curated metadata for standard SAP tables."""

    model_config = ConfigDict(extra="forbid")

    table: str
    description: str
    sap_module: str
    table_type: str = "transparent"
    key_fields: list[str] = Field(default_factory=list)
    important_fields: list[SapField] = Field(default_factory=list)
    common_joins: list[SapTableJoin] = Field(default_factory=list)
    related_business_concepts: list[str] = Field(default_factory=list)
    typical_business_processes: list[str] = Field(default_factory=list)
    typical_subject_area: str | None = None
    source: KnowledgeSource = KnowledgeSource.prebuilt
    verified: bool = False
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class SqlKeyword(BaseModel):
    """T-SQL syntax keyword, operator, or function reference."""

    model_config = ConfigDict(extra="forbid")

    keyword: str
    category: str
    aliases: list[str] = Field(default_factory=list)
    technical_meaning: str
    tsql_example: str | None = None
    performance_notes: str | None = None
    common_pitfalls: list[str] = Field(default_factory=list)
    source_chapter: str | None = None
    source: KnowledgeSource = KnowledgeSource.prebuilt
    verified: bool = False
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class SapCatalogRecord(BaseModel):
    """Extracted record from the SAP catalog PDF."""

    model_config = ConfigDict(extra="forbid")

    table: str
    descriptions: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    module: str
    category: CatalogCategory = CatalogCategory.business
    level: Literal["table_only"] = "table_only"


# ==============================================================================
# Vector Index Chunks and Hit Schemas
# ==============================================================================


class SapChunkMetadata(BaseModel):
    """Metadata specific to SAP catalog chunks."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["sap"] = "sap"
    table: str
    module: str
    category: CatalogCategory
    sections: list[str] = Field(default_factory=list)


class SqlChunkMetadata(BaseModel):
    """Metadata specific to SQL documentation chunks."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["sql"] = "sql"
    chapter: str
    section: str
    page_start: int
    page_end: int
    dialects: list[SqlDialect] = Field(default_factory=list)
    has_code: bool = False
    keywords: list[str] = Field(default_factory=list)


ChunkMetadata = Annotated[SapChunkMetadata | SqlChunkMetadata, Field(discriminator="type")]


class DocChunk(BaseModel):
    """Document chunk stored in chunks.jsonl for vector retrieval."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    index_name: Literal["sap", "sql"]
    text: str
    metadata: ChunkMetadata


class RetrievalHit(BaseModel):
    """Result hit from a vector index or exact catalog match."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    text: str
    score: float
    match_type: MatchType
    source_file: str
    page: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    untrusted: Literal[True] = True
    calibrated: bool = True


class SapLookupResult(BaseModel):
    """Result returned by lookup_sap for agents."""

    model_config = ConfigDict(extra="forbid")

    query: str
    found: bool
    level: Literal["field", "table", "table_only", "none"]
    table: str | None = None
    field: str | None = None
    entry: dict[str, Any] | None = None
    source: str | None = None
    confidence: float | None = None
    verified: bool = False
    notes: str | None = None


class SapSearchResult(BaseModel):
    """Result returned by SapRetriever.search for agents."""

    model_config = ConfigDict(extra="forbid")

    query: str
    exact_match_found: bool
    hits: list[RetrievalHit] = Field(default_factory=list)
    warning: str | None = None
    semantic_search_unavailable: bool = False
    unavailable_reason: str | None = None


# ==============================================================================
# Grounded Write-Back & Learned Knowledge Schemas
# ==============================================================================


class Evidence(BaseModel):
    """Grounding evidence required to persist learned entries."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    source_file: str
    page: int | None = None
    evidence_text: str
    retrieval_score: float
    match_type: MatchType


class LearnedSapEntry(BaseModel):
    """Learned SAP table/field knowledge derived from grounded retrieval."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    table: str
    field: str | None = None
    description: str
    business_meaning: str | None = None
    module: str | None = None
    evidence: Evidence
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal[KnowledgeSource.retrieved] = KnowledgeSource.retrieved
    status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: str
    last_seen_at: str
    hit_count: int = 1

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Description must be non-empty")
        if len(s) > 500:
            raise ValueError(f"Description length ({len(s)}) exceeds maximum allowed (500)")
        return s

    @model_validator(mode="after")
    def validate_grounding(self) -> LearnedSapEntry:
        ev_text = self.evidence.evidence_text.upper()
        tbl_upper = self.table.upper().strip()

        # 1. evidence_text must contain the table name
        if tbl_upper not in ev_text:
            raise ValueError(
                f"Grounding failure: evidence_text does not contain table '{self.table}'"
            )

        # 2. if field is set, evidence_text must contain field name
        if self.field:
            fld_upper = self.field.upper().strip()
            if fld_upper not in ev_text:
                raise ValueError(
                    f"Grounding failure: evidence_text does not contain field '{self.field}'"
                )

        # 3. match_type semantic requires retrieval_score >= 0.75
        if self.evidence.match_type == MatchType.semantic and self.evidence.retrieval_score < 0.75:
            raise ValueError(
                f"Grounding failure: semantic match score ({self.evidence.retrieval_score}) "
                f"is below write_back_min_confidence (0.75)"
            )

        if not self.id:
            tbl = self.table.upper().strip()
            fld = self.field.upper().strip() if self.field else "TABLE"
            self.id = f"{tbl}_{fld}"

        return self


class LearnedSqlEntry(BaseModel):
    """Learned SQL keyword knowledge derived from grounded retrieval."""

    model_config = ConfigDict(extra="forbid")

    keyword: str
    technical_meaning: str
    tsql_example: str | None = None
    evidence: Evidence
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal[KnowledgeSource.retrieved] = KnowledgeSource.retrieved
    created_at: str
    last_seen_at: str
    hit_count: int = 1


class SaveResult(BaseModel):
    """Result of saving a learned entry into persistent storage."""

    model_config = ConfigDict(extra="forbid")

    saved: bool
    action: Literal["created", "updated", "skipped", "rejected"]
    reason: str


# ==============================================================================
# Persisted JSON File Wrappers with schema_version
# ==============================================================================


class PrebuiltSapTablesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    tables: list[SapTable] = Field(default_factory=list)


class PrebuiltSapCommonFieldsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    common_fields: list[SapField] = Field(default_factory=list)
    custom_field_conventions: dict[str, str] = Field(default_factory=dict)


class PrebuiltSqlKeywordsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    keywords: list[SqlKeyword] = Field(default_factory=list)


class SapCatalogOverridesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    overrides: dict[str, str] = Field(default_factory=dict)
    ambiguous_lines: list[str] = Field(default_factory=list)


class LearnedSapTablesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    entries: list[LearnedSapEntry] = Field(default_factory=list)


class LearnedSqlKeywordsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    entries: list[LearnedSqlEntry] = Field(default_factory=list)


class GlossaryConceptGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str
    purpose: str
    key_points: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class GovernanceGlossaryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    subject_areas: GlossaryConceptGroup
    business_processes: GlossaryConceptGroup
    datasets: GlossaryConceptGroup
    rules: GlossaryConceptGroup
    business_concepts: GlossaryConceptGroup
    enforcements: GlossaryConceptGroup
    implementations: GlossaryConceptGroup
    opportunity_query: GlossaryConceptGroup
    error_query: GlossaryConceptGroup


class ProposedGlossaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concept_group: str
    value: str
    proposed_by: str = "agent"
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    created_at: str
    notes: str | None = None


class LearnedGlossaryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    entries: list[ProposedGlossaryEntry] = Field(default_factory=list)


class IndexManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 2
    provider: str = "OpenAI-compatible"
    index_name: str
    embedding_model: str
    dimensions: int
    base_url_hash: str
    normalized: bool = True
    source_file: str = ""
    source_sha256: str = ""
    chunk_count: int
    built_at: str
    chunk_id_scheme: str = "deterministic_v1"
