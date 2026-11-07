"""Configuration and settings for knowledge layer via pydantic-settings."""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_default_knowledge_dir() -> Path:
    """Finds the knowledge directory relative to repository root."""
    current = Path(__file__).resolve().parent
    for candidate in [
        current.parent.parent.parent / "knowledge",
        current.parent.parent / "knowledge",
        Path("knowledge"),
    ]:
        if candidate.exists():
            return candidate.resolve()
    return (current.parent.parent.parent / "knowledge").resolve()


class KnowledgeSettings(BaseSettings):
    """Knowledge layer settings configured via environment variables with prefix KNOW_."""

    model_config = SettingsConfigDict(
        env_prefix="KNOW_",
        env_file=Path(__file__).resolve().parents[3] / ".env",
        extra="ignore",
    )

    # Base directory paths
    knowledge_dir: Path = Field(default_factory=find_default_knowledge_dir)
    top_k: int = 5
    min_score_sap: float = 0.25
    min_score_sql: float = 0.25
    include_data_dictionary: bool = False
    sap_exact_match_first: bool = True
    write_back_min_confidence: float = 0.75
    tsql_boost: float = 1.25
    other_dialect_penalty: float = 0.75
    auto_rebuild: bool = False
    custom_learned_dir: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def check_deprecated_embedding_model(cls, data: object) -> object:
        if isinstance(data, dict) and "embedding_model" in data or os.environ.get("KNOW_EMBEDDING_MODEL"):
            warnings.warn(
                "KNOW_EMBEDDING_MODEL is deprecated and ignored; use EMBEDDING_MODEL instead",
                DeprecationWarning,
                stacklevel=2,
            )
        return data

    @property
    def calibration_path(self) -> Path:
        return self.knowledge_dir / "calibration" / "embedding_calibration.json"

    @property
    def eval_queries_path(self) -> Path:
        return self.knowledge_dir / "eval" / "retrieval_queries.json"

    @property
    def sap_prebuilt_path(self) -> Path:
        return self.knowledge_dir / "sap_tables_prebuilt.json"

    @property
    def sap_common_fields_path(self) -> Path:
        return self.knowledge_dir / "sap_common_fields.json"

    @property
    def sql_keywords_path(self) -> Path:
        return self.knowledge_dir / "sql_keywords.json"

    @property
    def governance_glossary_path(self) -> Path:
        return self.knowledge_dir / "governance_glossary.json"

    @property
    def sap_overrides_path(self) -> Path:
        return self.knowledge_dir / "overrides" / "sap_catalog_overrides.json"

    @property
    def verified_entries_path(self) -> Path:
        return self.knowledge_dir / "overrides" / "verified_entries.json"

    @property
    def learned_dir(self) -> Path:
        path = (
            self.custom_learned_dir
            if self.custom_learned_dir is not None
            else self.knowledge_dir / "learned"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def learned_sap_path(self) -> Path:
        return self.learned_dir / "sap_tables_learned.json"

    @property
    def learned_sql_path(self) -> Path:
        return self.learned_dir / "sql_keywords_learned.json"

    @property
    def learned_glossary_path(self) -> Path:
        return self.learned_dir / "learned_glossary.json"

    @property
    def audit_log_path(self) -> Path:
        return self.learned_dir / "audit.jsonl"

    @property
    def index_dir(self) -> Path:
        path = self.knowledge_dir / "index"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sap_catalog_path(self) -> Path:
        return self.index_dir / "sap_catalog.json"

    @property
    def sap_extraction_report_path(self) -> Path:
        return self.index_dir / "extraction_report.json"

    @property
    def sap_index_dir(self) -> Path:
        path = self.index_dir / "sap_docs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sql_index_dir(self) -> Path:
        path = self.index_dir / "sql_docs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def sap_pdf_path(self) -> Path:
        return self.knowledge_dir / "sources" / "sap_tables.pdf"

    @property
    def sql_pdf_path(self) -> Path:
        return self.knowledge_dir / "sources" / "sql_reference.pdf"


_settings_instance: KnowledgeSettings | None = None


def get_settings() -> KnowledgeSettings:
    """Returns a singleton instance of KnowledgeSettings."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = KnowledgeSettings()
    return _settings_instance
