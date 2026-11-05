"""SAP Table Catalog for looking up standard SAP table metadata."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Heuristic pattern for standard SAP tables: e.g. MARA, BUT000, T001W, BKPF, LFA1
HEURISTIC_SAP_PATTERN = re.compile(r"^[A-Z]{1,5}\d{0,3}[A-Z]?$")


class SapTableCatalog:
    """Catalog of known SAP tables and resolution utilities."""

    def __init__(self, catalog_path: Path | str | None = None) -> None:
        self._tables: dict[str, dict[str, str]] = {}
        path = self._resolve_catalog_path(catalog_path)
        if path and path.exists():
            self._load_from_json(path)
        else:
            logger.warning("SAP table catalog file not found at %s. Initialized empty.", path)

    @staticmethod
    def _resolve_catalog_path(catalog_path: Path | str | None) -> Path | None:
        if catalog_path:
            return Path(catalog_path)
        # Search relative to current file or repo root
        current_dir = Path(__file__).resolve().parent
        for candidate in [
            current_dir.parent.parent.parent / "knowledge" / "sap_table_names.json",
            current_dir.parent.parent / "knowledge" / "sap_table_names.json",
            Path("knowledge/sap_table_names.json"),
        ]:
            if candidate.exists():
                return candidate
        return None

    def _load_from_json(self, path: Path) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    name = item.get("name", "").upper()
                    if name:
                        self._tables[name] = {
                            "name": name,
                            "description": item.get("description", ""),
                            "module": item.get("module", ""),
                        }
            logger.info("Loaded %d SAP tables from catalog: %s", len(self._tables), path)
        except (OSError, json.JSONDecodeError) as e:
            logger.error("Failed to load SAP table catalog from %s: %e", path, e)

    def is_known(self, name: str) -> bool:
        """Check if an exact SAP table name exists in the catalog."""
        return name.upper().strip() in self._tables

    def describe(self, name: str) -> dict[str, str] | None:
        """Retrieve description and module for a known SAP table."""
        return self._tables.get(name.upper().strip())

    def resolve_sap_table(
        self,
        raw_name: str,
        naming_sap_table: str | None = None,
        is_cte: bool = False,
        is_temp: bool = False,
    ) -> tuple[str | None, float, bool]:
        r"""
        Resolves SAP table name according to:
        (1) naming engine's sap_table
        (2) exact catalog match
        (3) heuristic ^[A-Z]{1,5}\d{0,3}[A-Z]?$ marked low confidence
        (4) Z*/Y* tables marked custom
        Skip temp tables and CTE names.
        Returns: (sap_table_name, confidence, is_custom)
        """
        clean_name = raw_name.strip("[]").split(".")[-1].strip()

        if is_temp or is_cte or clean_name.startswith("#"):
            return None, 1.0, False

        # (1) Naming engine's sap_table
        if naming_sap_table:
            return naming_sap_table.upper(), 1.0, False

        clean_upper = clean_name.upper()

        # (2) Exact catalog match
        if self.is_known(clean_upper):
            return clean_upper, 1.0, False

        # (4) Z*/Y* custom tables
        if clean_upper.startswith(("Z", "Y")):
            return clean_upper, 0.8, True

        # (3) Heuristic
        if HEURISTIC_SAP_PATTERN.match(clean_upper):
            return clean_upper, 0.4, False

        return None, 0.0, False

    def determine_role(
        self,
        raw_name: str,
        naming_kind: str | None = None,
        is_cte: bool = False,
        is_temp: bool = False,
        sap_table: str | None = None,
    ) -> str:
        """Determine table role in the migration context."""
        clean_name = raw_name.strip("[]").split(".")[-1].strip()

        if is_temp or clean_name.startswith("#"):
            return "temp"
        if is_cte:
            return "cte"
        if naming_kind in ("source_staging", "target_staging"):
            return "sap_staging"
        if naming_kind == "mapping_reference":
            return "mapping_reference"
        if naming_kind == "external_source":
            return "external_source"
        if sap_table and (self.is_known(sap_table) or HEURISTIC_SAP_PATTERN.match(sap_table)):
            return "sap_native"
        return "unknown"
