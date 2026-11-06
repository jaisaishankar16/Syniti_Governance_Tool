"""SAPDomainAgent definition, prompt loader, and input builder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, AgentOutputSchema

from app.agents.models import AgentDeps, SAPAnalysis
from app.agents.tools import (
    lookup_sap_knowledge,
    save_learned_sap_knowledge,
    search_sap_catalog,
)

if TYPE_CHECKING:
    from agents import Model

    from app.agents.knowledge_pack import SapKnowledgePack
    from app.agents.settings import LLMSettings
    from app.parser.models import ParsedQuery


def load_sap_prompt() -> tuple[str, str]:
    """Loads sap_agent_v1.md prompt and computes version hash."""
    prompt_path = Path(__file__).parent / "prompts" / "sap_agent_v1.md"
    content = prompt_path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    version = f"sap_agent-v1+{content_hash}"
    return content, version


def build_sap_agent_input(parsed: ParsedQuery, pack: SapKnowledgePack) -> str:
    """Builds a deterministic delimited input string for SAPDomainAgent."""
    sections = [
        "<task>",
        (
            "Analyze the SAP domain semantics, staging table mappings, business concepts, "
            "and client handling for this query. Output only a JSON object adhering to SAPAnalysis."
        ),
        "</task>",
        "<query_facts>",
        json.dumps(parsed.sap_part, indent=2),
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


def create_sap_agent(
    model: Model | str,
    settings: LLMSettings,
) -> tuple[Agent[AgentDeps], str]:
    """Creates a configured SAPDomainAgent instance."""
    prompt_content, prompt_version = load_sap_prompt()

    from app.agents.capabilities import resolve_effective_modes

    effective_struct, effective_tools, _ = resolve_effective_modes(settings)

    if effective_tools == "prefetch":
        tools: list[Any] = []
    else:
        tools = [lookup_sap_knowledge, search_sap_catalog]
        if settings.learning_enabled:
            tools.append(save_learned_sap_knowledge)

    output_type: Any = None
    if effective_struct == "native":
        try:
            output_type = AgentOutputSchema(SAPAnalysis, strict_json_schema=True)
        except Exception:  # noqa: BLE001
            output_type = AgentOutputSchema(SAPAnalysis, strict_json_schema=False)

    agent: Agent[AgentDeps] = Agent(
        name="SAPDomainAgent",
        instructions=prompt_content,
        tools=tools,
        model=model,
        output_type=output_type,
    )
    return agent, prompt_version
