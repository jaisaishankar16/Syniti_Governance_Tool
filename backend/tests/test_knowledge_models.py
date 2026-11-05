"""Unit tests for Phase 2 Pydantic models, validation rules, and schemas."""

import pytest
from app.knowledge.models import (
    Evidence,
    LearnedSapEntry,
    MatchType,
    SapLookupResult,
    SapSearchResult,
    SapTable,
)
from pydantic import ValidationError


def test_learned_sap_entry_rejects_missing_table_in_evidence():
    """Evidence text must contain table name (case-insensitive)."""
    ev = Evidence(
        chunk_id="chunk_1",
        source_file="sap_tables.pdf",
        evidence_text="This text describes materials in general without naming the table.",
        retrieval_score=0.9,
        match_type=MatchType.exact,
    )
    with pytest.raises(ValidationError, match="does not contain table 'MARA'"):
        LearnedSapEntry(
            table="MARA",
            description="General Material Data",
            evidence=ev,
            confidence=0.85,
            created_at="2026-11-04T00:00:00Z",
            last_seen_at="2026-11-04T00:00:00Z",
        )


def test_learned_sap_entry_rejects_missing_field_in_evidence():
    """Evidence text must contain field name when field is set."""
    ev = Evidence(
        chunk_id="chunk_2",
        source_file="sap_tables.pdf",
        evidence_text="Table MARA contains general material data.",
        retrieval_score=0.9,
        match_type=MatchType.exact,
    )
    with pytest.raises(ValidationError, match="does not contain field 'MATNR'"):
        LearnedSapEntry(
            table="MARA",
            field="MATNR",
            description="Material Number",
            evidence=ev,
            confidence=0.85,
            created_at="2026-11-04T00:00:00Z",
            last_seen_at="2026-11-04T00:00:00Z",
        )


def test_learned_sap_entry_rejects_low_score_semantic():
    """Semantic match requires score >= 0.75."""
    ev = Evidence(
        chunk_id="chunk_3",
        source_file="sap_tables.pdf",
        evidence_text="Table MARA material master",
        retrieval_score=0.70,  # Below threshold 0.75
        match_type=MatchType.semantic,
    )
    with pytest.raises(ValidationError, match="below write_back_min_confidence"):
        LearnedSapEntry(
            table="MARA",
            description="Material Master",
            evidence=ev,
            confidence=0.70,
            created_at="2026-11-04T00:00:00Z",
            last_seen_at="2026-11-04T00:00:00Z",
        )


def test_learned_sap_entry_rejects_empty_or_too_long_description():
    """Description must be non-empty and <= 500 characters."""
    ev = Evidence(
        chunk_id="chunk_4",
        source_file="sap_tables.pdf",
        evidence_text="Table MARA material master",
        retrieval_score=0.85,
        match_type=MatchType.semantic,
    )
    with pytest.raises(ValidationError, match="Description must be non-empty"):
        LearnedSapEntry(
            table="MARA",
            description="   ",
            evidence=ev,
            confidence=0.85,
            created_at="2026-11-04T00:00:00Z",
            last_seen_at="2026-11-04T00:00:00Z",
        )

    with pytest.raises(ValidationError, match="exceeds maximum allowed"):
        LearnedSapEntry(
            table="MARA",
            description="x" * 501,
            evidence=ev,
            confidence=0.85,
            created_at="2026-11-04T00:00:00Z",
            last_seen_at="2026-11-04T00:00:00Z",
        )


def test_models_forbid_extra_fields():
    """Extra fields must be rejected per ConfigDict(extra='forbid')."""
    ev = Evidence(
        chunk_id="chunk_5",
        source_file="sap_tables.pdf",
        evidence_text="Table MARA material master",
        retrieval_score=0.85,
        match_type=MatchType.semantic,
    )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LearnedSapEntry(
            table="MARA",
            description="Material Master",
            evidence=ev,
            confidence=0.85,
            created_at="2026-11-04T00:00:00Z",
            last_seen_at="2026-11-04T00:00:00Z",
            unexpected_field="not_allowed",  # type: ignore[call-arg]
        )


def test_json_schema_export():
    """Tool-facing models must export clean JSON schemas."""
    schema_lookup = SapLookupResult.model_json_schema()
    assert "properties" in schema_lookup
    assert "found" in schema_lookup["properties"]

    schema_search = SapSearchResult.model_json_schema()
    assert "properties" in schema_search
    assert "exact_match_found" in schema_search["properties"]

    schema_table = SapTable.model_json_schema()
    assert "properties" in schema_table
    assert "key_fields" in schema_table["properties"]
