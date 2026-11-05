"""Ingestion engine for SAP table catalog PDF (13-page reference)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pdfplumber

from .models import CatalogCategory, SapCatalogRecord
from .settings import KnowledgeSettings

logger = logging.getLogger(__name__)

SECTION_CONFIG = [
    ("Sales and Distribution Tables", "SD", CatalogCategory.business),
    ("Materials Management Tables", "MM", CatalogCategory.business),
    ("Purchasing Tables", "MM-PUR", CatalogCategory.business),
    ("Finance Tables", "FI/CO", CatalogCategory.business),
    ("Production Planning", "PP", CatalogCategory.business),
    ("Project Systems Tables", "PS", CatalogCategory.business),
    ("PM \\ SD Tables", "PM", CatalogCategory.business),
    ("PM \\\\ SD Tables", "PM", CatalogCategory.business),
    ("System Tables", "BASIS", CatalogCategory.system),
    ("Reports", "REPORTS", CatalogCategory.report),
    ("ABAP dic list", "BASIS", CatalogCategory.data_dictionary),
]

GLUED_LINE_REGEX = re.compile(r"([a-z\)])([A-Z]{2,8}\s+[A-Za-z].*)$")
STANDARD_TABLE_REGEX = re.compile(r"^([A-Z0-9_]{2,16})\s+(.+)$")


class SapCatalogIngest:
    """Parses, repairs, normalizes, and indexes the 13-page SAP tables PDF catalog."""

    def __init__(self, settings: KnowledgeSettings | None = None) -> None:
        self.settings = settings or KnowledgeSettings()
        self.overrides = self._load_overrides()

    def coverage_check(self, table_names: list[str]) -> dict[str, list[str]]:
        """Checks a list of table names against prebuilt and catalog tables."""
        return coverage_check(table_names, self.settings)

    def _load_overrides(self) -> dict[str, str]:
        path = self.settings.sap_overrides_path
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("overrides", {})
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Failed to load SAP overrides from %s: %s", path, e)
        return {
            "VTBFHAPO": "Transaction Flow",
            "VTBFHAZU": "Transaction Activity",
            "VTBFINKO": "Transaction Condition",
            "VTIFHAPO": "Underlying transaction flows",
            "VTIFHAZU": "Underlying transaction status table",
            "VIAUFKST": "Generated Table for View VIAUFKST",
        }

    def repair_glued_line(self, line: str) -> list[str]:
        """Repairs glued lines like '...Schedule LinesEKKN Account Assignment...' using regex."""
        # Check specific glued fixtures first
        if "LinesEKKN " in line:
            parts = line.split("LinesEKKN ")
            return [f"{parts[0]}Lines", f"EKKN {parts[1]}"]
        if "SettlementCOBRB " in line:
            parts = line.split("SettlementCOBRB ")
            return [f"{parts[0]}Settlement", f"COBRB {parts[1]}"]

        match = GLUED_LINE_REGEX.search(line)
        if match:
            first = line[: match.start(2)].strip()
            second = line[match.start(2) :].strip()
            return [first, second]
        return [line]

    def ingest(self, pdf_path: Path | None = None) -> list[SapCatalogRecord]:
        """Extracts and normalizes all table records from the SAP PDF catalog."""
        path = pdf_path or self.settings.sap_pdf_path
        if not path.exists():
            raise FileNotFoundError(f"SAP PDF catalog not found at {path}")

        raw_lines: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                txt = page.extract_text()
                if txt:
                    raw_lines.extend(txt.splitlines())

        records_map: dict[str, SapCatalogRecord] = {}
        repaired_lines: list[dict[str, str]] = []
        unmatched_lines: list[str] = []
        overrides_applied: list[dict[str, str]] = []

        current_section = "Sales and Distribution Tables"
        current_module = "SD"
        current_category = CatalogCategory.business
        last_table_name: str | None = None

        for raw_line in raw_lines:
            line = raw_line.strip()
            if not line:
                continue

            # 1. Remove [ GO TO TOP ]
            if line == "[ GO TO TOP ]":
                continue

            # 2. Section Heading Detection
            matched_sec = False
            for sec_title, mod, cat in SECTION_CONFIG:
                if sec_title.lower() in line.lower():
                    current_section = sec_title
                    current_module = mod
                    current_category = cat
                    matched_sec = True
                    break
            if matched_sec:
                continue

            # 3. Handle glued line heuristic
            split_lines = self.repair_glued_line(line)
            if len(split_lines) > 1:
                repaired_lines.append({"original": line, "repaired": " || ".join(split_lines)})

            for sub_line in split_lines:
                sub_line = sub_line.strip()
                if not sub_line:
                    continue

                # 4. Check overrides (for entries without space)
                override_hit = False
                for ov_table, ov_desc in self.overrides.items():
                    if sub_line.startswith(ov_table):
                        override_hit = True
                        overrides_applied.append({"table": ov_table, "description": ov_desc})
                        self._add_or_merge_record(
                            records_map=records_map,
                            table_name=ov_table,
                            description=ov_desc,
                            section=current_section,
                            module=current_module,
                            category=current_category,
                        )
                        last_table_name = ov_table
                        break
                if override_hit:
                    continue

                # 5. Match standard "TABLE description"
                match = STANDARD_TABLE_REGEX.match(sub_line)
                if match:
                    tbl_name = match.group(1).upper()
                    desc = match.group(2).strip()
                    self._add_or_merge_record(
                        records_map=records_map,
                        table_name=tbl_name,
                        description=desc,
                        section=current_section,
                        module=current_module,
                        category=current_category,
                    )
                    last_table_name = tbl_name
                    continue

                # 6. Continuation line wrapped from previous entry
                if last_table_name and last_table_name in records_map:
                    rec = records_map[last_table_name]
                    if rec.descriptions:
                        rec.descriptions[-1] = f"{rec.descriptions[-1]} {sub_line}".strip()
                    else:
                        rec.descriptions.append(sub_line)
                else:
                    unmatched_lines.append(sub_line)

        records_list = list(records_map.values())

        # Persist extracted catalog to /knowledge/index/sap_catalog.json
        self.settings.sap_catalog_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.settings.sap_catalog_path, "w", encoding="utf-8") as f:
            catalog_dump = [r.model_dump() for r in records_list]
            json.dump(catalog_dump, f, indent=2, ensure_ascii=False)

        # Persist audit extraction report
        categories_count: dict[str, int] = {}
        modules_count: dict[str, int] = {}
        for r in records_list:
            categories_count[r.category.value] = categories_count.get(r.category.value, 0) + 1
            modules_count[r.module] = modules_count.get(r.module, 0) + 1

        report = {
            "total_extracted": len(records_list),
            "repaired_lines_count": len(repaired_lines),
            "repaired_lines": repaired_lines,
            "overrides_applied_count": len(overrides_applied),
            "overrides_applied": overrides_applied,
            "unmatched_lines_count": len(unmatched_lines),
            "unmatched_lines": unmatched_lines[:50],
            "categories_breakdown": categories_count,
            "modules_breakdown": modules_count,
        }
        with open(self.settings.sap_extraction_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

        logger.info("Extracted %d unique SAP table records from %s", len(records_list), path)
        return records_list

    @staticmethod
    def _add_or_merge_record(
        records_map: dict[str, SapCatalogRecord],
        table_name: str,
        description: str,
        section: str,
        module: str,
        category: CatalogCategory,
    ) -> None:
        """Merges records for tables appearing across multiple sections (e.g. MAST, AFKO, KONV)."""
        clean_desc = description.strip()
        cat_to_use = CatalogCategory.data_dictionary if table_name.startswith("DD") else category
        mod_to_use = "BASIS" if table_name.startswith("DD") else module

        if table_name not in records_map:
            records_map[table_name] = SapCatalogRecord(
                table=table_name,
                descriptions=[clean_desc] if clean_desc else [],
                sections=[section],
                module=mod_to_use,
                category=cat_to_use,
                level="table_only",
            )
        else:
            existing = records_map[table_name]
            if clean_desc and clean_desc not in existing.descriptions:
                existing.descriptions.append(clean_desc)
            if section not in existing.sections:
                existing.sections.append(section)


def coverage_check(
    table_names: list[str], settings: KnowledgeSettings | None = None
) -> dict[str, list[str]]:
    """Checks a list of table names against prebuilt and catalog tables."""
    settings = settings or KnowledgeSettings()
    covered_prebuilt: list[str] = []
    covered_catalog: list[str] = []
    missing: list[str] = []

    # 1. Load prebuilt tables
    prebuilt_tables: set[str] = set()
    if settings.sap_prebuilt_path.exists():
        try:
            with open(settings.sap_prebuilt_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for t in data.get("tables", []):
                prebuilt_tables.add(t.get("table", "").upper())
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed reading prebuilt tables: %s", e)

    # 2. Load catalog tables
    catalog_tables: set[str] = set()
    if settings.sap_catalog_path.exists():
        try:
            with open(settings.sap_catalog_path, "r", encoding="utf-8") as f:
                cat_data = json.load(f)
            for item in cat_data:
                catalog_tables.add(item.get("table", "").upper())
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed reading catalog tables: %s", e)

    for item_t in table_names:
        raw_t: str = ""
        if isinstance(item_t, dict):
            raw_t = item_t.get("name") or item_t.get("table") or ""
        elif isinstance(item_t, str):
            raw_t = item_t
        if not raw_t:
            continue
        clean = raw_t.upper().strip("[]").split(".")[-1].strip()
        if clean in prebuilt_tables:
            covered_prebuilt.append(clean)
        elif clean in catalog_tables:
            covered_catalog.append(clean)
        else:
            missing.append(clean)

    return {
        "covered_by_prebuilt": sorted(set(covered_prebuilt)),
        "covered_by_catalog": sorted(set(covered_catalog)),
        "missing": sorted(set(missing)),
    }
