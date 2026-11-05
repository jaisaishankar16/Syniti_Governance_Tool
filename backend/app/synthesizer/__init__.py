"""Phase 4 Synthesizer package for executive business intelligence and governance rule creation."""

from app.synthesizer.deterministic import (
    baseline_opportunity_query,
    risk_floor,
)
from app.synthesizer.fewshot import (
    FewShotExample,
    format_fewshot_for_prompt,
    load_allowed_fewshot_examples,
)
from app.synthesizer.models import (
    BusinessConcept,
    BusinessIntelligence,
    BusinessIntelligenceMeta,
    CacheDetail,
    GovernanceOutput,
    IsNewFlags,
    PromptVersions,
    RiskSummary,
    SynthesizerRawOutput,
    TokenUsageBreakdown,
    ValidationSummary,
)
from app.synthesizer.synthesizer import (
    build_synthesizer_input,
    create_synthesizer_agent,
    synthesize,
)

__all__ = [
    "BusinessConcept",
    "BusinessIntelligence",
    "BusinessIntelligenceMeta",
    "CacheDetail",
    "FewShotExample",
    "GovernanceOutput",
    "IsNewFlags",
    "PromptVersions",
    "RiskSummary",
    "SynthesizerRawOutput",
    "TokenUsageBreakdown",
    "ValidationSummary",
    "baseline_opportunity_query",
    "build_synthesizer_input",
    "create_synthesizer_agent",
    "format_fewshot_for_prompt",
    "load_allowed_fewshot_examples",
    "risk_floor",
    "synthesize",
]
