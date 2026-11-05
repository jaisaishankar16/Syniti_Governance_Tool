"""SQLSemanticsAgent definition, prompt loader, and input builder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, AgentOutputSchema

from app.agents.models import AgentDeps, SQLAnalysis
from app.agents.tools import lookup_sql_keyword, search_sql_docs

if TYPE_CHECKING:
    from agents import Model

    from app.agents.knowledge_pack import SqlKnowledgePack
    from app.agents.settings import LLMSettings
    from app.parser.models import ParsedQuery


def load_sql_prompt() -> tuple[str, str]:
    """Loads sql_agent_v1.md prompt and computes version hash."""
    prompt_path = Path(__file__).parent / "prompts" / "sql_agent_v1.md"
    content = prompt_path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    version = f"sql_agent-v1+{content_hash}"
    return content, version


def build_sql_agent_input(parsed: ParsedQuery, pack: SqlKnowledgePack) -> str:
    """Builds a deterministic delimited input string for SQLSemanticsAgent."""
    sections = [
        "<task>",
        (
            "Analyze the technical semantics, query structure, joins, predicates, derivations, "
            "and potential risks for this T-SQL statement. Output only a JSON object adhering to SQLAnalysis."
        ),
        "</task>",
        "<query_facts>",
        json.dumps(parsed.sql_part, indent=2),
        "</query_facts>",
        "<sql_data>",
        parsed.normalized_sql,
        "</sql_data>",
        "<knowledge_pack>",
        pack.model_dump_json(indent=2),
        "</knowledge_pack>",
        "<unresolved>",
        json.dumps([u.model_dump() for u in pack.unresolved], indent=2),
        "</unresolved>",
    ]

    if parsed.comments:
        sections.extend(
            [
                "<untrusted_comments>",
                json.dumps(parsed.comments, indent=2),
                "</untrusted_comments>",
            ]
        )

    return "\n\n".join(sections)


def create_sql_agent(
    model: Model | str,
    settings: LLMSettings,
) -> tuple[Agent[AgentDeps], str]:
    """Creates a configured SQLSemanticsAgent instance."""
    prompt_content, prompt_version = load_sql_prompt()

    output_type: Any = None
    if settings.structured_output_mode == "native":
        try:
            output_type = AgentOutputSchema(SQLAnalysis, strict_json_schema=True)
        except Exception:  # noqa: BLE001
            output_type = AgentOutputSchema(SQLAnalysis, strict_json_schema=False)

    agent: Agent[AgentDeps] = Agent(
        name="SQLSemanticsAgent",
        instructions=prompt_content,
        tools=[lookup_sql_keyword, search_sql_docs],
        model=model,
        output_type=output_type,
    )
    return agent, prompt_version
