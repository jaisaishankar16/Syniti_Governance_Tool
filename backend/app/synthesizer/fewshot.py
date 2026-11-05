"""Few-shot example selection and formatting for the Phase 4 Synthesizer.

Uses ONLY Sheet1, Sheet5, Sheet7, and Sheet8 from backend/tests/fixtures/examples.json.
All other sheets (Sheet2, Sheet3, Sheet4, Sheet6, Sheet9, Sheet10, Sheet11) stay strictly held out for testing.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ALLOWED_FEWSHOT_SHEETS = {"sheet1", "sheet5", "sheet7", "sheet8"}


class FewShotExample(BaseModel):
    """Structured few-shot training example for synthesizer prompt grounding."""

    model_config = ConfigDict(extra="forbid")

    sheet_id: str
    input_sql: str
    subject_area: str
    business_process: str
    dataset: str
    concept_term: str
    rule: str
    specification: str
    enforcement: str
    implementation: str
    opportunity_definition: str
    error_definition: str
    opportunity_query: str
    error_query: str


def _get_fixtures_path() -> Path:
    # Try resolving relative to this file
    p = Path(__file__).resolve().parents[3] / "backend" / "tests" / "fixtures" / "examples.json"
    if p.exists():
        return p
    alt = Path("backend/tests/fixtures/examples.json").resolve()
    if alt.exists():
        return alt
    raise FileNotFoundError("Could not locate backend/tests/fixtures/examples.json")


def load_allowed_fewshot_examples() -> list[FewShotExample]:
    """Loads and returns only the 4 allowed few-shot examples (Sheet1, Sheet5, Sheet7, Sheet8)."""
    fixture_path = _get_fixtures_path()
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    examples: list[FewShotExample] = []
    for item in data:
        sheet_id = str(item.get("id", "")).lower()
        if sheet_id in ALLOWED_FEWSHOT_SHEETS:
            exp = item.get("expected", {})
            examples.append(
                FewShotExample(
                    sheet_id=item.get("id", ""),
                    input_sql=item.get("input_sql", ""),
                    subject_area=exp.get("Subject Area", ""),
                    business_process=exp.get("Business Process", ""),
                    dataset=exp.get("Dataset", ""),
                    concept_term=exp.get("Business Concept (Term)", ""),
                    rule=exp.get("Rule", ""),
                    specification=exp.get("Specification", ""),
                    enforcement=exp.get("Enforcement", ""),
                    implementation=exp.get("Implementation", ""),
                    opportunity_definition=exp.get("Opportunity Definition", ""),
                    error_definition=exp.get("Error Definition", ""),
                    opportunity_query=exp.get("Opportunity Query", ""),
                    error_query=exp.get("Error Query", ""),
                )
            )

    return examples


def format_fewshot_for_prompt() -> str:
    """Renders the allowed few-shot examples as markdown text for the synthesizer prompt."""
    examples = load_allowed_fewshot_examples()
    blocks: list[str] = []

    for ex in examples:
        block = f"""### Reference Example ({ex.sheet_id})
Input Query Pattern:
```sql
{ex.input_sql}
```
Expected Governance Synthesis:
- Subject Area: {ex.subject_area}
- Business Process: {ex.business_process}
- Dataset: {ex.dataset}
- Business Concept: {ex.concept_term}
- Rule: {ex.rule}
- Specification: {ex.specification}
- Enforcement: {ex.enforcement}
- Implementation: {ex.implementation}
- Opportunity Definition: {ex.opportunity_definition}
- Error Definition: {ex.error_definition}
- Opportunity Query:
```sql
{ex.opportunity_query}
```
- Error Query:
```sql
{ex.error_query}
```"""
        blocks.append(block)

    return "\n\n".join(blocks)
