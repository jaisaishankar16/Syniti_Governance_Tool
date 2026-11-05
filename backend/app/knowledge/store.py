"""Knowledge store providing unified SAP/SQL lookups and grounded write-back with file locking."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from filelock import FileLock
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.knowledge.models import (
    LearnedSapEntry,
    LearnedSapTablesFile,
    LearnedSqlKeywordsFile,
    PrebuiltSapCommonFieldsFile,
    PrebuiltSapTablesFile,
    PrebuiltSqlKeywordsFile,
    SapCatalogRecord,
    SapField,
    SapLookupResult,
    SapTable,
    SaveResult,
    SqlKeyword,
)
from app.knowledge.settings import KnowledgeSettings, get_settings

T = TypeVar("T", bound=BaseModel)
F = TypeVar("F", bound=BaseModel)


class LearnedStore(Generic[T, F]):
    """Thread-safe and process-safe store for learned entries using filelock and atomic writes."""

    def __init__(
        self,
        file_path: Path,
        file_model: type[F],
        key_fn: Callable[[T], tuple[Any, ...]],
        settings: KnowledgeSettings | None = None,
    ) -> None:
        self.file_path = file_path
        self.lock_path = file_path.with_suffix(".lock")
        self.file_model = file_model
        self.key_fn = key_fn
        self.settings = settings or get_settings()

        self._lock = threading.Lock()
        self._mtime: float = -1.0
        self._entries: list[T] = []

    def _load_if_stale(self) -> None:
        if not self.file_path.exists():
            self._entries = []
            self._mtime = 0.0
            return

        current_mtime = self.file_path.stat().st_mtime
        if current_mtime > self._mtime:
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    file_obj = self.file_model.model_validate_json(f.read())
                    self._entries = list(getattr(file_obj, "entries", []))
                    self._mtime = current_mtime
            except (OSError, ValidationError, ValueError):
                pass

    def get_entries(self) -> list[T]:
        """Returns all entries, reloading from disk if modified by another process."""
        with self._lock:
            self._load_if_stale()
            return list(self._entries)

    def find(self, predicate: Callable[[T], bool]) -> T | None:
        """Finds first entry satisfying predicate."""
        with self._lock:
            self._load_if_stale()
            for e in self._entries:
                if predicate(e):
                    return e
            return None

    def save_entry(self, entry: T) -> tuple[bool, str, str]:
        """Saves or updates an entry atomically under file lock.

        Returns (saved, action, reason) where action is 'created' | 'updated' | 'skipped' | 'rejected'.
        """
        with self._lock:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.lock_path), timeout=15):
                # Reload fresh data under lock
                entries: list[T] = []
                if self.file_path.exists():
                    try:
                        with open(self.file_path, "r", encoding="utf-8") as f:
                            file_obj = self.file_model.model_validate_json(f.read())
                            entries = list(getattr(file_obj, "entries", []))
                    except (OSError, ValidationError, ValueError):
                        entries = []

                entry_key = self.key_fn(entry)
                existing_idx = -1
                for idx, e in enumerate(entries):
                    if self.key_fn(e) == entry_key:
                        existing_idx = idx
                        break

                now_str = datetime.now(timezone.utc).isoformat()
                action = "created"
                reason = "Created new learned entry"

                if existing_idx != -1:
                    existing = entries[existing_idx]
                    # Update existing entry
                    attr_conf = "confidence"
                    attr_desc = "description"
                    attr_last_seen = "last_seen_at"
                    attr_hit_count = "hit_count"
                    attr_status = "status"

                    new_conf = float(getattr(entry, attr_conf, 0.0))
                    old_conf = float(getattr(existing, attr_conf, 0.0))
                    hit_count = int(getattr(existing, attr_hit_count, 1)) + 1

                    if hasattr(entry, attr_status):
                        setattr(existing, attr_status, getattr(entry, attr_status))

                    if new_conf > old_conf:
                        setattr(existing, attr_conf, new_conf)
                        if hasattr(entry, attr_desc):
                            setattr(existing, attr_desc, getattr(entry, attr_desc))
                        if hasattr(existing, attr_last_seen):
                            setattr(existing, attr_last_seen, now_str)
                        if hasattr(existing, attr_hit_count):
                            setattr(existing, attr_hit_count, hit_count)
                        action = "updated"
                        reason = f"Updated existing entry with higher confidence ({old_conf} -> {new_conf})"
                    else:
                        if hasattr(existing, attr_last_seen):
                            setattr(existing, attr_last_seen, now_str)
                        if hasattr(existing, attr_hit_count):
                            setattr(existing, attr_hit_count, hit_count)
                        action = "updated"
                        reason = f"Bumped hit count for existing entry ({hit_count})"
                else:
                    entries.append(entry)
                    action = "created"
                    reason = "Saved new learned entry"

                # Atomic write
                file_obj = self.file_model(schema_version=1, entries=entries)  # type: ignore[call-arg]
                tmp_path = self.file_path.with_suffix(f".tmp_{os.getpid()}_{threading.get_ident()}")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(file_obj.model_dump_json(indent=2))
                os.replace(tmp_path, self.file_path)

                self._entries = entries
                self._mtime = self.file_path.stat().st_mtime

                return True, action, reason


class KnowledgeStore:
    """Single entry point for agent knowledge retrieval and write-back."""

    def __init__(self, settings: KnowledgeSettings | None = None) -> None:
        self.settings = settings or get_settings()

        # Learned stores
        self.sap_learned_store = LearnedStore[LearnedSapEntry, LearnedSapTablesFile](
            file_path=self.settings.learned_sap_path,
            file_model=LearnedSapTablesFile,
            key_fn=lambda e: (e.table.upper(), e.field.upper() if e.field else None),
            settings=self.settings,
        )
        self.sql_learned_store = LearnedStore[SqlKeyword, LearnedSqlKeywordsFile](
            file_path=self.settings.learned_sql_path,
            file_model=LearnedSqlKeywordsFile,
            key_fn=lambda e: (e.keyword.upper(),),
            settings=self.settings,
        )

        # Prebuilt and catalog caches
        self._prebuilt_sap: dict[str, SapTable] = {}
        self._common_fields: dict[str, SapField] = {}
        self._custom_field_conventions: dict[str, str] = {}
        self._prebuilt_sql: dict[str, SqlKeyword] = {}
        self._catalog_tables: dict[str, SapCatalogRecord] = {}
        self._all_known_fields: set[str] = set()

        self._load_prebuilts()

    def _load_prebuilts(self) -> None:
        verified_tables: set[str] = set()
        verified_fields: set[str] = set()
        verified_keywords: set[str] = set()
        if (
            hasattr(self.settings, "verified_entries_path")
            and self.settings.verified_entries_path.exists()
        ):
            try:
                with open(self.settings.verified_entries_path, "r", encoding="utf-8") as vf:
                    v_data = json.loads(vf.read())
                    verified_tables = {t.upper() for t in v_data.get("verified_tables", [])}
                    verified_fields = {f.upper() for f in v_data.get("verified_fields", [])}
                    verified_keywords = {k.upper() for k in v_data.get("verified_keywords", [])}
            except (OSError, json.JSONDecodeError) as e:
                logging.getLogger(__name__).warning("Failed to load verified_entries.json: %s", e)

        # 1. SAP Prebuilt Tables
        if self.settings.sap_prebuilt_path.exists():
            with open(self.settings.sap_prebuilt_path, "r", encoding="utf-8") as f:
                prebuilt_file = PrebuiltSapTablesFile.model_validate_json(f.read())
                for tbl in prebuilt_file.tables:
                    tbl_name = tbl.table.upper()
                    if verified_tables:
                        tbl.verified = tbl_name in verified_tables
                        tbl.confidence = 1.0 if tbl.verified else 0.70
                    self._prebuilt_sap[tbl_name] = tbl
                    for fld in tbl.important_fields:
                        if verified_fields:
                            fld.verified = (fld.field.upper() in verified_fields) or tbl.verified
                        self._all_known_fields.add(fld.field.upper())
                    for k in tbl.key_fields:
                        self._all_known_fields.add(k.upper())

        # 2. SAP Common Fields
        if self.settings.sap_common_fields_path.exists():
            with open(self.settings.sap_common_fields_path, "r", encoding="utf-8") as f:
                cf_file = PrebuiltSapCommonFieldsFile.model_validate_json(f.read())
                for cf in cf_file.common_fields:
                    cf_name = cf.field.upper()
                    if verified_fields:
                        cf.verified = cf_name in verified_fields
                    self._common_fields[cf_name] = cf
                    self._all_known_fields.add(cf_name)
                self._custom_field_conventions = dict(cf_file.custom_field_conventions)

        # 3. SQL Keywords Prebuilt
        if self.settings.sql_keywords_path.exists():
            with open(self.settings.sql_keywords_path, "r", encoding="utf-8") as f:
                sql_file = PrebuiltSqlKeywordsFile.model_validate_json(f.read())
                for kw in sql_file.keywords:
                    kw_name = kw.keyword.upper()
                    if verified_keywords:
                        kw.verified = kw_name in verified_keywords
                        kw.confidence = 1.0 if kw.verified else 0.70
                    self._prebuilt_sql[kw_name] = kw
                    for alias in kw.aliases:
                        self._prebuilt_sql[alias.upper()] = kw

        # 4. SAP Catalog Records
        if self.settings.sap_catalog_path.exists():
            with open(self.settings.sap_catalog_path, "r", encoding="utf-8") as f:
                cat_records = TypeAdapter(list[SapCatalogRecord]).validate_json(f.read())
                for rec in cat_records:
                    self._catalog_tables[rec.table.upper()] = rec

    def lookup_sap(self, table: str, field: str | None = None) -> SapLookupResult:
        """Looks up SAP table or field in order: prebuilt -> learned -> common fields -> catalog."""
        tbl_clean = table.strip().upper()
        fld_clean = field.strip().upper() if field else None
        query_str = f"{table}.{field}" if field else table

        # 1. Prebuilt Tables
        if tbl_clean in self._prebuilt_sap:
            tbl = self._prebuilt_sap[tbl_clean]
            if fld_clean is None:
                return SapLookupResult(
                    query=query_str,
                    found=True,
                    level="table",
                    table=tbl.table,
                    entry=tbl.model_dump(),
                    source="prebuilt",
                    confidence=tbl.confidence,
                    verified=tbl.verified,
                    notes=f"Found {tbl.table} in prebuilt SAP catalog (verified={tbl.verified})",
                )
            # Field specified: check important fields and key fields
            for fld in tbl.important_fields:
                if fld.field.upper() == fld_clean:
                    return SapLookupResult(
                        query=query_str,
                        found=True,
                        level="field",
                        table=tbl.table,
                        field=fld.field,
                        entry=fld.model_dump(),
                        source="prebuilt",
                        confidence=tbl.confidence,
                        verified=getattr(fld, "verified", tbl.verified),
                        notes=f"Found {fld.field} in prebuilt fields for {tbl.table}",
                    )
            if fld_clean in [k.upper() for k in tbl.key_fields]:
                return SapLookupResult(
                    query=query_str,
                    found=True,
                    level="field",
                    table=tbl.table,
                    field=fld_clean,
                    entry={"field": fld_clean, "key": True, "verified": tbl.verified},
                    source="prebuilt",
                    confidence=tbl.confidence,
                    verified=tbl.verified,
                    notes=f"Field {fld_clean} is a key field in prebuilt table {tbl.table}",
                )

        # 2. Learned Store
        if fld_clean is None:
            learned_tbl = self.sap_learned_store.find(
                lambda e: e.table.upper() == tbl_clean and e.field is None
            )
            if learned_tbl:
                is_v = getattr(learned_tbl, "status", "pending") == "approved"
                return SapLookupResult(
                    query=query_str,
                    found=True,
                    level="table",
                    table=learned_tbl.table,
                    entry=learned_tbl.model_dump(),
                    source="learned",
                    confidence=learned_tbl.confidence,
                    verified=is_v,
                    notes="Found in learned SAP store",
                )
        else:
            learned_fld = self.sap_learned_store.find(
                lambda e: bool(
                    e.table.upper() == tbl_clean and e.field and e.field.upper() == fld_clean
                )
            )
            if learned_fld:
                is_v = getattr(learned_fld, "status", "pending") == "approved"
                return SapLookupResult(
                    query=query_str,
                    found=True,
                    level="field",
                    table=learned_fld.table,
                    field=fld_clean,
                    entry=learned_fld.model_dump(),
                    source="learned",
                    confidence=learned_fld.confidence,
                    verified=is_v,
                    notes="Found in learned SAP store",
                )

        # 3. Common Fields & Custom Conventions
        if fld_clean is not None:
            if fld_clean in self._common_fields:
                cf = self._common_fields[fld_clean]
                notes = f"Found in common cross-table fields: {cf.description}"
                if cf.business_meaning:
                    notes += f". {cf.business_meaning}"
                is_v = getattr(cf, "verified", False)
                return SapLookupResult(
                    query=query_str,
                    found=True,
                    level="field",
                    table=tbl_clean if tbl_clean else None,
                    field=cf.field,
                    entry=cf.model_dump(),
                    source="prebuilt_common_fields",
                    confidence=1.0 if is_v else 0.70,
                    verified=is_v,
                    notes=notes,
                )

            # Custom field convention matching:
            # Explicit special conventions
            if fld_clean == "ZACTIVE":
                return SapLookupResult(
                    query=query_str,
                    found=True,
                    level="field",
                    table=tbl_clean,
                    field="ZACTIVE",
                    entry={
                        "field": "ZACTIVE",
                        "description": "Migration active flag",
                        "is_custom": True,
                    },
                    source="custom_convention",
                    confidence=0.9,
                    notes="Matches convention ZACTIVE: migration active flag",
                )
            if fld_clean == "ZLEGACYWERKS":
                return SapLookupResult(
                    query=query_str,
                    found=True,
                    level="field",
                    table=tbl_clean,
                    field="ZLEGACYWERKS",
                    entry={
                        "field": "ZLEGACYWERKS",
                        "description": "Legacy plant",
                        "is_custom": True,
                    },
                    source="custom_convention",
                    confidence=0.9,
                    notes="Matches convention ZLEGACYWERKS: legacy plant",
                )

            # Z<FIELD> convention: shadow of known base field
            if fld_clean.startswith("Z") and len(fld_clean) > 1:
                base_field = fld_clean[1:]
                if base_field in self._all_known_fields:
                    notes = (
                        f"Matches convention Z<FIELD>: migration shadow of base field {base_field}"
                    )
                    entry = {
                        "field": fld_clean,
                        "base_field": base_field,
                        "convention": "Z<FIELD>",
                        "is_custom": True,
                    }
                    return SapLookupResult(
                        query=query_str,
                        found=True,
                        level="field",
                        table=tbl_clean,
                        field=fld_clean,
                        entry=entry,
                        source="custom_convention",
                        confidence=0.85,
                        notes=notes,
                    )

            # <FIELD>NEW convention: derived target value of known base field
            if fld_clean.endswith("NEW") and len(fld_clean) > 3:
                base_field = fld_clean[:-3]
                if base_field in self._all_known_fields:
                    notes = f"Matches convention <FIELD>NEW: derived target value of base field {base_field}"
                    entry = {
                        "field": fld_clean,
                        "base_field": base_field,
                        "convention": "<FIELD>NEW",
                        "is_custom": True,
                    }
                    return SapLookupResult(
                        query=query_str,
                        found=True,
                        level="field",
                        table=tbl_clean,
                        field=fld_clean,
                        entry=entry,
                        source="custom_convention",
                        confidence=0.85,
                        notes=notes,
                    )

        # 4. SAP Catalog (table-level only)
        if tbl_clean in self._catalog_tables:
            cat_rec = self._catalog_tables[tbl_clean]
            if fld_clean is None:
                return SapLookupResult(
                    query=query_str,
                    found=True,
                    level="table_only",
                    table=cat_rec.table,
                    entry=cat_rec.model_dump(),
                    source="catalog",
                    confidence=0.7,
                    notes="Found in SAP catalog at table-only level; fields are explicitly unknown",
                )
            # Table is in catalog, but field is unknown and matches no convention: do not guess!
            return SapLookupResult(
                query=query_str,
                found=False,
                level="none",
                table=cat_rec.table,
                field=fld_clean,
                entry=None,
                source="catalog",
                confidence=None,
                notes=f"Table {cat_rec.table} is in catalog at table_only level, but field {fld_clean} is unknown and matches no convention",
            )

        # 5. Not found
        return SapLookupResult(
            query=query_str,
            found=False,
            level="none",
            table=tbl_clean if tbl_clean else None,
            field=fld_clean,
            notes="Not found in prebuilt, learned, common fields, or catalog",
        )

    def lookup_sql_keyword(self, term: str) -> SqlKeyword | None:
        """Looks up a SQL keyword in prebuilt then learned."""
        term_clean = term.strip().upper()

        # 1. Prebuilt
        if term_clean in self._prebuilt_sql:
            return self._prebuilt_sql[term_clean]

        # 2. Learned
        learned = self.sql_learned_store.find(
            lambda k: (
                k.keyword.upper() == term_clean or term_clean in [a.upper() for a in k.aliases]
            )
        )
        return learned

    def save_learned_sap(self, entry: LearnedSapEntry | dict[str, Any]) -> SaveResult:
        """Saves a learned SAP table or field entry with grounding validation, prebuilt protection, and audit logging."""
        # Validate Pydantic model
        if isinstance(entry, dict):
            try:
                validated_entry = LearnedSapEntry.model_validate(entry)
            except ValidationError as exc:
                return SaveResult(
                    saved=False,
                    action="rejected",
                    reason=f"Grounding validation failed: {exc}",
                )
        else:
            validated_entry = entry

        tbl_clean = validated_entry.table.upper()
        fld_clean = validated_entry.field.upper() if validated_entry.field else None

        # Prebuilt protection: never overwrite prebuilt
        if tbl_clean in self._prebuilt_sap:
            prebuilt_tbl = self._prebuilt_sap[tbl_clean]
            if fld_clean is None:
                return SaveResult(
                    saved=False,
                    action="rejected",
                    reason=f"Table {tbl_clean} is already defined in prebuilt SAP tables and cannot be overwritten",
                )
            # If field is specified and defined in prebuilt table or common fields:
            if fld_clean in [f.field.upper() for f in prebuilt_tbl.important_fields]:
                return SaveResult(
                    saved=False,
                    action="rejected",
                    reason=f"Field {tbl_clean}.{fld_clean} is already defined in prebuilt SAP tables and cannot be overwritten",
                )
            if fld_clean in [k.upper() for k in prebuilt_tbl.key_fields]:
                return SaveResult(
                    saved=False,
                    action="rejected",
                    reason=f"Field {tbl_clean}.{fld_clean} is a key field in prebuilt SAP tables and cannot be overwritten",
                )

        if fld_clean and fld_clean in self._common_fields:
            return SaveResult(
                saved=False,
                action="rejected",
                reason=f"Field {fld_clean} is defined in prebuilt common fields and cannot be overwritten",
            )

        # Save to learned store
        saved, action, reason = self.sap_learned_store.save_entry(validated_entry)

        # Append to audit log
        self._append_audit_log(
            action=action,
            entity_type="sap",
            table=tbl_clean,
            field=fld_clean,
            confidence=validated_entry.confidence,
            evidence=validated_entry.evidence.model_dump(),
        )

        return SaveResult(saved=saved, action=action, reason=reason)  # type: ignore[arg-type]

    def _append_audit_log(
        self,
        action: str,
        entity_type: str,
        table: str | None,
        field: str | None,
        confidence: float,
        evidence: dict[str, Any],
    ) -> None:
        """Appends an audit entry to /knowledge/learned/audit.jsonl under file lock."""
        audit_path = self.settings.audit_log_path
        audit_lock = audit_path.with_suffix(".lock")
        audit_path.parent.mkdir(parents=True, exist_ok=True)

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "entity_type": entity_type,
            "table": table,
            "field": field,
            "confidence": confidence,
            "evidence": evidence,
        }

        with FileLock(str(audit_lock), timeout=15), open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def list_learned_sap(self, status: str | None = None) -> list[LearnedSapEntry]:
        """Lists learned SAP entries, optionally filtered by status ('pending', 'approved', 'rejected')."""
        entries = self.sap_learned_store.get_entries()
        if status:
            return [e for e in entries if getattr(e, "status", "pending") == status]
        return entries

    def set_learned_sap_status(
        self,
        entry_id: str,
        status: Literal["approved", "rejected"],
        auditor: str = "system",
    ) -> bool:
        """Approves or rejects a learned SAP entry, updating disk and logging an audit event."""
        entries = self.sap_learned_store.get_entries()
        target: LearnedSapEntry | None = None
        for e in entries:
            eid = (
                getattr(e, "id", None)
                or f"{e.table.upper()}_{e.field.upper() if e.field else 'TABLE'}"
            )
            if eid.upper() == entry_id.upper() or (getattr(e, "id", None) == entry_id):
                target = e
                break

        if target is None:
            return False

        target.status = status
        self.sap_learned_store.save_entry(target)
        self._append_audit_log(
            action=f"{status}_by_{auditor}",
            entity_type="sap",
            table=target.table,
            field=target.field,
            confidence=target.confidence,
            evidence={"auditor": auditor, "status": status, "id": entry_id},
        )
        return True

    def get_glossary_proposals(self) -> list[dict[str, Any]]:
        """Returns all proposed governance glossary terms."""
        from app.knowledge.glossary import GlossaryHelper

        helper = GlossaryHelper(self.settings)
        return helper.get_proposals()
