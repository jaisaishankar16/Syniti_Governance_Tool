"""Tests for KnowledgeStore, write-back, concurrency, prebuilt protection, and coverage."""

import concurrent.futures
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.knowledge.models import (
    Evidence,
    LearnedSapEntry,
    LearnedSapTablesFile,
    MatchType,
    StaleIndexError,
)
from app.knowledge.settings import KnowledgeSettings, get_settings
from app.knowledge.store import KnowledgeStore
from app.knowledge.vector_index import VectorIndex


@pytest.fixture
def temp_store(tmp_path: Path) -> KnowledgeStore:
    """Fixture providing an isolated KnowledgeStore with its own learned directory."""
    settings = KnowledgeSettings(
        knowledge_dir=get_settings().knowledge_dir,
        custom_learned_dir=tmp_path / "learned",
    )
    return KnowledgeStore(settings=settings)


def test_grounded_save_and_immediate_lookup(temp_store: KnowledgeStore):
    """Grounded save succeeds and is visible to immediate lookup without restart."""
    now = datetime.now(timezone.utc).isoformat()
    ev = Evidence(
        chunk_id="chunk_test_1",
        source_file="sap_tables.pdf",
        evidence_text="Table Z_CUSTOM_STAGING stores staging records.",
        retrieval_score=0.88,
        match_type=MatchType.semantic,
    )
    entry = LearnedSapEntry(
        table="Z_CUSTOM_STAGING",
        description="Custom staging table",
        evidence=ev,
        confidence=0.88,
        created_at=now,
        last_seen_at=now,
    )

    res = temp_store.save_learned_sap(entry)
    assert res.saved is True
    assert res.action == "created"

    lookup_res = temp_store.lookup_sap("Z_CUSTOM_STAGING")
    assert lookup_res.found is True
    assert lookup_res.level == "table"
    assert lookup_res.source == "learned"
    assert lookup_res.confidence == 0.88


def test_prebuilt_cannot_be_overwritten(temp_store: KnowledgeStore):
    """Prebuilt tables and fields cannot be overwritten by learned entries."""
    now = datetime.now(timezone.utc).isoformat()
    ev_table = Evidence(
        chunk_id="c_tbl",
        source_file="sap_tables.pdf",
        evidence_text="Table MARA is material master",
        retrieval_score=1.0,
        match_type=MatchType.exact,
    )
    res_table = temp_store.save_learned_sap(
        LearnedSapEntry(
            table="MARA",
            description="Attempt to overwrite prebuilt table",
            evidence=ev_table,
            confidence=0.95,
            created_at=now,
            last_seen_at=now,
        )
    )
    assert res_table.saved is False
    assert res_table.action == "rejected"
    assert "already defined in prebuilt" in res_table.reason

    ev_field = Evidence(
        chunk_id="c_fld",
        source_file="sap_tables.pdf",
        evidence_text="Field MATNR in table MARA",
        retrieval_score=1.0,
        match_type=MatchType.exact,
    )
    res_field = temp_store.save_learned_sap(
        LearnedSapEntry(
            table="MARA",
            field="MATNR",
            description="Attempt to overwrite prebuilt field",
            evidence=ev_field,
            confidence=0.95,
            created_at=now,
            last_seen_at=now,
        )
    )
    assert res_field.saved is False
    assert res_field.action == "rejected"
    assert "already defined in prebuilt" in res_field.reason


def test_deduplication_and_confidence_raising(temp_store: KnowledgeStore):
    """Duplicate learned entries are deduped; confidence is updated only if new is higher."""
    now = datetime.now(timezone.utc).isoformat()
    ev1 = Evidence(
        chunk_id="c_dedupe_1",
        source_file="sap_tables.pdf",
        evidence_text="Table Z_DEDUPE_TBL first mention",
        retrieval_score=0.80,
        match_type=MatchType.semantic,
    )
    res1 = temp_store.save_learned_sap(
        LearnedSapEntry(
            table="Z_DEDUPE_TBL",
            description="First definition",
            evidence=ev1,
            confidence=0.80,
            created_at=now,
            last_seen_at=now,
        )
    )
    assert res1.saved is True
    assert res1.action == "created"

    # Save lower confidence -> confidence unchanged, hit count incremented
    ev2 = Evidence(
        chunk_id="c_dedupe_2",
        source_file="sap_tables.pdf",
        evidence_text="Table Z_DEDUPE_TBL second mention",
        retrieval_score=0.76,
        match_type=MatchType.semantic,
    )
    res2 = temp_store.save_learned_sap(
        LearnedSapEntry(
            table="Z_DEDUPE_TBL",
            description="Lower confidence definition",
            evidence=ev2,
            confidence=0.76,
            created_at=now,
            last_seen_at=now,
        )
    )
    assert res2.saved is True
    assert res2.action == "updated"

    lookup1 = temp_store.lookup_sap("Z_DEDUPE_TBL")
    assert lookup1.confidence == 0.80
    assert lookup1.entry is not None
    assert lookup1.entry.get("hit_count") == 2

    # Save higher confidence -> confidence raised, description updated
    ev3 = Evidence(
        chunk_id="c_dedupe_3",
        source_file="sap_tables.pdf",
        evidence_text="Table Z_DEDUPE_TBL third mention with high confidence",
        retrieval_score=0.95,
        match_type=MatchType.semantic,
    )
    res3 = temp_store.save_learned_sap(
        LearnedSapEntry(
            table="Z_DEDUPE_TBL",
            description="Highest confidence definition",
            evidence=ev3,
            confidence=0.95,
            created_at=now,
            last_seen_at=now,
        )
    )
    assert res3.saved is True
    assert res3.action == "updated"

    lookup2 = temp_store.lookup_sap("Z_DEDUPE_TBL")
    assert lookup2.confidence == 0.95
    assert lookup2.entry is not None
    assert lookup2.entry.get("description") == "Highest confidence definition"
    assert lookup2.entry.get("hit_count") == 3


def test_concurrent_writers_20(temp_store: KnowledgeStore):
    """20 concurrent writers leave valid JSON with no lost entries and 20 audit log lines."""
    now = datetime.now(timezone.utc).isoformat()

    def write_task(idx: int) -> bool:
        tbl = f"Z_CONCURRENT_{idx:02d}"
        ev = Evidence(
            chunk_id=f"c_conc_{idx}",
            source_file="sap_tables.pdf",
            evidence_text=f"Table {tbl} concurrent record",
            retrieval_score=0.85,
            match_type=MatchType.semantic,
        )
        entry = LearnedSapEntry(
            table=tbl,
            description=f"Concurrent table {idx}",
            evidence=ev,
            confidence=0.85,
            created_at=now,
            last_seen_at=now,
        )
        res = temp_store.save_learned_sap(entry)
        return res.saved

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(write_task, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert all(results)
    assert len(results) == 20

    # Verify JSON file has exactly 20 entries
    with open(temp_store.sap_learned_store.file_path, "r", encoding="utf-8") as f:
        file_obj = LearnedSapTablesFile.model_validate_json(f.read())
    assert len(file_obj.entries) == 20

    # Verify audit log has exactly 20 lines
    audit_path = temp_store.settings.audit_log_path
    with open(audit_path, "r", encoding="utf-8") as f:
        audit_lines = [line.strip() for line in f if line.strip()]
    assert len(audit_lines) == 20


def test_stale_manifest_raises_stale_index_error(tmp_path: Path):
    """A modified or mismatched manifest raises StaleIndexError on load."""
    settings = get_settings()
    # Copy existing sql_docs index into tmp_path
    tmp_index_dir = tmp_path / "sql_docs"
    shutil.copytree(settings.sql_index_dir, tmp_index_dir)

    manifest_file = tmp_index_dir / "manifest.json"
    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    # Invalidate embedding model in manifest
    manifest_data["embedding_model"] = "obsolete-model-v0"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)

    vec_idx = VectorIndex(
        index_name="sql_docs",
        index_dir=tmp_index_dir,
        source_path=settings.sql_pdf_path,
        settings=settings,
    )

    with pytest.raises(StaleIndexError, match="Embedding model mismatch"):
        vec_idx.load(auto_rebuild=False)


def test_phase1_examples_coverage():
    """Every SAP table resolved from the 11 Excel examples is found by lookup_sap at least at table level."""
    examples_path = Path(__file__).resolve().parent / "fixtures" / "examples.json"
    if not examples_path.exists():
        pytest.skip("examples.json fixture not found")

    with open(examples_path, "r", encoding="utf-8") as f:
        examples = json.load(f)

    from app.parser.sql_parser import SqlParser

    parser = SqlParser()
    store = KnowledgeStore()

    resolved_tables = set()
    for ex in examples:
        parsed = parser.parse(ex["input_sql"])
        if parsed.view_naming and parsed.view_naming.sap_table:
            resolved_tables.add(parsed.view_naming.sap_table)
        for t in parsed.tables:
            if t.naming and t.naming.sap_table:
                resolved_tables.add(t.naming.sap_table)

    assert len(resolved_tables) > 0, "Should resolve SAP tables from 11 examples"
    for tbl in resolved_tables:
        res = store.lookup_sap(tbl)
        assert res.found is True, f"SAP table {tbl} from Excel examples not found in store"
        assert res.level in ("table", "table_only")

    # Unknown Z field without a convention must return found=False (not a guess)
    z_res = store.lookup_sap("MARA", "ZCOMPLETELY_UNKNOWN_CUSTOM_FIELD")
    assert z_res.found is False, "Unknown Z field must return found=False"


def test_verified_status_and_overrides():
    """Phase 2.1: prebuilts have verified=false unless listed in verified_entries.json."""
    store = KnowledgeStore()

    # Verified table (MARA in verified_entries.json)
    mara_res = store.lookup_sap("MARA")
    assert mara_res.found is True
    assert mara_res.verified is True
    assert mara_res.confidence == 1.0

    # Unverified table (BUT000 not in verified_entries.json)
    but_res = store.lookup_sap("BUT000")
    assert but_res.found is True
    assert but_res.verified is False
    assert but_res.confidence == 0.70

    # Verified field (MANDT) vs unverified field (ERDAT)
    mandt_res = store.lookup_sap("", "MANDT")
    assert mandt_res.found is True
    assert mandt_res.verified is True
    assert mandt_res.confidence == 1.0

    erdat_res = store.lookup_sap("", "ERDAT")
    assert erdat_res.found is True
    assert erdat_res.verified is False
    assert erdat_res.confidence == 0.70

    # Verified SQL keyword (WHERE) vs unverified (CROSS APPLY)
    kw_where = store.lookup_sql_keyword("WHERE")
    assert kw_where is not None
    assert kw_where.verified is True
    assert kw_where.confidence == 1.0

    kw_apply = store.lookup_sql_keyword("CROSS APPLY")
    assert kw_apply is not None
    assert kw_apply.verified is False
    assert kw_apply.confidence == 0.70
