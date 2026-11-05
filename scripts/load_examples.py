#!/usr/bin/env python3
"""Script to extract test fixture examples from DQ_Queries.xlsx into examples.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import openpyxl  # type: ignore[import-untyped]

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

EXPECTED_OUTPUT_LABELS = [
    "Subject Area",
    "Business Process",
    "Dataset",
    "Business Concept (Term)",
    "Rule",
    "Specification",
    "Enforcement",
    "Implementation",
    "Opportunity Definition",
    "Error Definition",
    "Opportunity Query",
    "Error Query",
]


def load_examples(excel_path: Path, output_json_path: Path | None = None) -> list[dict[str, Any]]:
    """Reads Excel fixture and writes examples.json."""
    if not excel_path.exists():
        raise FileNotFoundError(f"Fixture file not found: {excel_path}")

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    examples: list[dict[str, Any]] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        row_map: dict[str, str] = {}

        for row_idx in range(1, ws.max_row + 1):
            cell_a = ws.cell(row=row_idx, column=1).value
            cell_b = ws.cell(row=row_idx, column=2).value
            if cell_a is not None:
                label = str(cell_a).strip()
                val = str(cell_b).strip() if cell_b is not None else ""
                row_map[label] = val

        # Extract input query
        input_sql = ""
        for label, val in row_map.items():
            if label.startswith("Migration Query"):
                input_sql = val
                break

        if not input_sql:
            logger.warning("Sheet %s: Missing 'Migration Query' input SQL", sheet_name)

        # Extract expected outputs
        expected: dict[str, str] = {}
        for expected_label in EXPECTED_OUTPUT_LABELS:
            if expected_label in row_map:
                expected[expected_label] = row_map[expected_label]
            else:
                logger.warning("Sheet %s: Missing expected label '%s'", sheet_name, expected_label)
                expected[expected_label] = ""

        examples.append(
            {
                "id": sheet_name,
                "input_sql": input_sql,
                "expected": expected,
            }
        )

    if output_json_path:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(examples, f, indent=2, ensure_ascii=False)
        logger.info("Successfully extracted %d examples to %s", len(examples), output_json_path)

    return examples


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    excel_path = repo_root / "backend" / "tests" / "fixtures" / "DQ_Queries.xlsx"
    output_path = repo_root / "backend" / "tests" / "fixtures" / "examples.json"
    load_examples(excel_path, output_path)


if __name__ == "__main__":
    main()
