#!/usr/bin/env python3
"""Script to validate all knowledge JSON files through Pydantic v2 models and write REVIEW.md."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure backend package is in python path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "backend"))

from app.knowledge.models import (
    GovernanceGlossaryFile,
    LearnedGlossaryFile,
    LearnedSapTablesFile,
    LearnedSqlKeywordsFile,
    PrebuiltSapCommonFieldsFile,
    PrebuiltSapTablesFile,
    PrebuiltSqlKeywordsFile,
    SapCatalogOverridesFile,
)
from app.knowledge.settings import KnowledgeSettings
from pydantic import ValidationError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def validate_all(settings: KnowledgeSettings | None = None) -> bool:
    settings = settings or KnowledgeSettings()
    k_dir = settings.knowledge_dir
    review_items: dict[str, list[dict[str, Any]]] = {}
    has_errors = False

    logger.info("Validating knowledge directory at: %s", k_dir)

    # 1. Validate sap_tables_prebuilt.json
    path = settings.sap_prebuilt_path
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            model = PrebuiltSapTablesFile.model_validate(data)
            tables_seen = set()
            for t in model.tables:
                if t.table in tables_seen:
                    logger.error("Duplicate SAP table '%s' in %s", t.table, path.name)
                    has_errors = True
                tables_seen.add(t.table)
                if not t.verified:
                    review_items.setdefault("sap_tables_prebuilt.json", []).append(
                        {"item": t.table, "reason": "verified=false", "module": t.sap_module}
                    )
            logger.info("Validated %s: %d tables", path.name, len(model.tables))
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to validate %s: %s", path.name, e)
            has_errors = True
    else:
        logger.error("Missing file: %s", path)
        has_errors = True

    # 2. Validate sap_common_fields.json
    path = settings.sap_common_fields_path
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            model_fields = PrebuiltSapCommonFieldsFile.model_validate(data)
            fields_seen = set()
            for fld in model_fields.common_fields:
                if fld.field in fields_seen:
                    logger.error("Duplicate field '%s' in %s", fld.field, path.name)
                    has_errors = True
                fields_seen.add(fld.field)
            logger.info("Validated %s: %d fields", path.name, len(model_fields.common_fields))
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to validate %s: %s", path.name, e)
            has_errors = True
    else:
        logger.error("Missing file: %s", path)
        has_errors = True

    # 3. Validate sql_keywords.json
    path = settings.sql_keywords_path
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            model_sql = PrebuiltSqlKeywordsFile.model_validate(data)
            keywords_seen = set()
            for kw in model_sql.keywords:
                if kw.keyword in keywords_seen:
                    logger.error("Duplicate keyword '%s' in %s", kw.keyword, path.name)
                    has_errors = True
                keywords_seen.add(kw.keyword)
                if not kw.verified:
                    review_items.setdefault("sql_keywords.json", []).append(
                        {
                            "item": kw.keyword,
                            "reason": "verified=false",
                            "chapter": kw.source_chapter,
                        }
                    )
            logger.info("Validated %s: %d keywords", path.name, len(model_sql.keywords))
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to validate %s: %s", path.name, e)
            has_errors = True
    else:
        logger.error("Missing file: %s", path)
        has_errors = True

    # 4. Validate governance_glossary.json
    path = settings.governance_glossary_path
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            GovernanceGlossaryFile.model_validate(data)
            logger.info("Validated %s: all 9 concept groups verified", path.name)
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to validate %s: %s", path.name, e)
            has_errors = True
    else:
        logger.error("Missing file: %s", path)
        has_errors = True

    # 5. Validate sap_catalog_overrides.json
    path = settings.sap_overrides_path
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            SapCatalogOverridesFile.model_validate(data)
            logger.info("Validated %s", path.name)
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to validate %s: %s", path.name, e)
            has_errors = True

    # 6. Validate learned files if present
    if settings.learned_sap_path.exists():
        try:
            with open(settings.learned_sap_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            LearnedSapTablesFile.model_validate(data)
            logger.info("Validated %s", settings.learned_sap_path.name)
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to validate %s: %s", settings.learned_sap_path.name, e)
            has_errors = True

    if settings.learned_sql_path.exists():
        try:
            with open(settings.learned_sql_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            LearnedSqlKeywordsFile.model_validate(data)
            logger.info("Validated %s", settings.learned_sql_path.name)
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to validate %s: %s", settings.learned_sql_path.name, e)
            has_errors = True

    if settings.learned_glossary_path.exists():
        try:
            with open(settings.learned_glossary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            LearnedGlossaryFile.model_validate(data)
            logger.info("Validated %s", settings.learned_glossary_path.name)
        except (OSError, ValidationError, ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to validate %s: %s", settings.learned_glossary_path.name, e)
            has_errors = True

    # Write REVIEW.md
    review_path = k_dir / "REVIEW.md"
    write_review_md(review_path, review_items)
    logger.info("Generated review document at %s", review_path)

    if has_errors:
        logger.error("Validation failed with errors.")
        return False

    logger.info("All knowledge files validated successfully.")
    return True


def write_review_md(review_path: Path, review_items: dict[str, list[dict[str, Any]]]) -> None:
    lines = [
        "# Knowledge Base Review Queue",
        "",
        "This file tracks knowledge base records that have `verified: false` or require manual domain review.",
        "",
    ]
    if not review_items:
        lines.append("All knowledge base entries are verified! Zero pending reviews.\n")
    else:
        for filename, items in review_items.items():
            lines.append(f"## {filename}")
            lines.append("")
            lines.append("| Item | Reason | Metadata |")
            lines.append("| :--- | :--- | :--- |")
            for item in items:
                name = item.get("item", "")
                reason = item.get("reason", "")
                extra = ", ".join(
                    f"{k}={v}" for k, v in item.items() if k not in ("item", "reason")
                )
                lines.append(f"| `{name}` | {reason} | {extra} |")
            lines.append("")

    with open(review_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> None:
    success = validate_all()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
