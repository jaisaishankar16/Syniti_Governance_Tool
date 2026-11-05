"""Tests for prompt preparation, input inspection, and held-out dataset isolation."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.knowledge_pack import build_sap_pack, build_sql_pack
from app.agents.sap_agent import load_sap_prompt
from app.agents.sql_agent import load_sql_prompt
from app.knowledge.store import KnowledgeStore
from app.parser.sql_parser import SqlParser
from app.synthesizer.fewshot import ALLOWED_FEWSHOT_SHEETS, load_allowed_fewshot_examples
from app.synthesizer.synthesizer import _load_prompt_template as load_synth_prompt


def test_held_out_sheets_never_appear_in_prompts_fewshot_or_packs() -> None:
    """Asserts that held-out sheets (2, 3, 4, 6, 9, 10, 11) NEVER appear in system prompts,

    few-shot examples, or prebuilt knowledge packs.
    """
    held_out_ids = {"sheet2", "sheet3", "sheet4", "sheet6", "sheet9", "sheet10", "sheet11"}

    # 1. Verify fewshot allowed list strictly excludes held-out sheets
    assert ALLOWED_FEWSHOT_SHEETS.isdisjoint(held_out_ids)
    fewshot_examples = load_allowed_fewshot_examples()
    for ex in fewshot_examples:
        sheet_clean = ex.sheet_id.lower().replace(" ", "").replace("_", "")
        assert sheet_clean not in held_out_ids, (
            f"Held-out sheet {ex.sheet_id} found in few-shot examples!"
        )

    # 2. Verify system prompt templates do not mention held-out sheets
    sql_prompt, _ = load_sql_prompt()
    sap_prompt, _ = load_sap_prompt()
    synth_prompt, _ = load_synth_prompt()

    for sheet in held_out_ids:
        assert sheet not in sql_prompt.lower(), f"Held-out sheet {sheet} found in SQL prompt!"
        assert sheet not in sap_prompt.lower(), f"Held-out sheet {sheet} found in SAP prompt!"
        assert sheet not in synth_prompt.lower(), (
            f"Held-out sheet {sheet} found in Synthesizer prompt!"
        )

    # 3. Verify knowledge store prebuilt files do not leak held-out rule text
    repo_root = Path(__file__).resolve().parents[2]
    fixtures_path = repo_root / "backend" / "tests" / "fixtures" / "examples.json"
    with open(fixtures_path, "r", encoding="utf-8") as f:
        all_examples = json.load(f)

    held_out_rules: list[str] = []
    for ex in all_examples:
        sid = (
            str(ex.get("id", ex.get("source_sheet", ""))).lower().replace(" ", "").replace("_", "")
        )
        if sid in held_out_ids:
            rule_text = ex.get("rule_name") or ex.get("expected", {}).get("Rule")
            if rule_text and len(rule_text) > 10:
                held_out_rules.append(rule_text.strip().lower())

    store = KnowledgeStore()
    parser = SqlParser()
    parsed_sample = parser.parse("SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S]")
    sql_pack = build_sql_pack(parsed_sample, store)
    sap_pack = build_sap_pack(parsed_sample, store)

    sql_dump = sql_pack.model_dump_json().lower()
    sap_dump = sap_pack.model_dump_json().lower()

    for rule in held_out_rules:
        assert rule not in sql_dump, f"Held-out rule text '{rule}' found in SQL knowledge pack!"
        assert rule not in sap_dump, f"Held-out rule text '{rule}' found in SAP knowledge pack!"


def test_inspect_inputs_cli_functionality() -> None:
    """Verifies that _handle_inspect_inputs runs cleanly without raising and outputs expected sections."""
    import io
    import sys

    from app.agents.settings import LLMSettings
    from app.orchestrator.cli import _handle_inspect_inputs

    sql = "SELECT MARC.MATNR, MARA.MTART FROM MM_MARC_ECC_EU_R1_MARC_S MARC INNER JOIN MM_MARA_ECC_EU_R1_MARA_S MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"
    settings = LLMSettings()

    buf = io.StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = buf
        _handle_inspect_inputs(sql, "test_sql", settings)
    finally:
        sys.stdout = old_stdout

    output = buf.getvalue()
    assert "INSPECT INPUTS & PROMPT PREP" in output
    assert "1. SQL PARSER OUTPUT:" in output
    assert "2. PROMPT TEMPLATES & VERSIONS:" in output
    assert "3. KNOWLEDGE PACKS:" in output
    assert "4. TOKEN COUNT ESTIMATES" in output
    assert "Max Single-Turn Context" in output
