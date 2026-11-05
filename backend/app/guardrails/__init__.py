"""Dedicated guardrails layer for MDM Syniti: safety, security, budget, and resilience."""

from app.guardrails.agent_output import guardrail_sap_output, guardrail_sql_output
from app.guardrails.budget import (
    BudgetExceededError,
    BudgetTracker,
    ConcurrencyManager,
    InMemoryRateLimiter,
    TokenUsage,
    calculate_cost_usd,
    get_budget_tracker,
)
from app.guardrails.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    get_circuit_breaker,
    is_transient_error,
)
from app.guardrails.final_output import (
    sanitize_text,
    validate_canary_and_secrets,
    validate_glossary_membership,
    validate_literal_grounding,
    validate_risk_consistency_floor,
)
from app.guardrails.injection import (
    generate_boundary_id,
    generate_canary_token,
    neutralize_delimiters,
    scan_canary_leak,
    scan_prompt_injection,
)
from app.guardrails.input_checks import (
    check_input_limits,
    check_looks_like_sql,
    sanitize_input_characters,
)
from app.guardrails.logging_filter import (
    GuardrailLoggingFilter,
    mask_secrets_in_text,
    mask_sql_in_text,
    structlog_guardrail_processor,
)
from app.guardrails.models import (
    GuardrailBlocked,
    GuardrailEvent,
    GuardrailReport,
    KeyValue,
    SanitizedInput,
    Verdict,
)
from app.guardrails.pipeline import post_flight_pipeline, pre_flight_pipeline
from app.guardrails.redaction import check_and_handle_pii, redact_secrets
from app.guardrails.settings import GuardrailSettings, get_guardrail_settings
from app.guardrails.sql_safety import (
    validate_error_subset_of_opportunity,
    validate_generated_sql,
)
from app.guardrails.tool_policy import (
    check_learned_write_allowed,
    validate_tool_arguments,
    validate_tool_call_permission,
)

__all__ = [
    "BudgetExceededError",
    "BudgetTracker",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "ConcurrencyManager",
    "GuardrailBlocked",
    "GuardrailEvent",
    "GuardrailLoggingFilter",
    "GuardrailReport",
    "GuardrailSettings",
    "InMemoryRateLimiter",
    "KeyValue",
    "SanitizedInput",
    "TokenUsage",
    "Verdict",
    "calculate_cost_usd",
    "check_and_handle_pii",
    "check_input_limits",
    "check_learned_write_allowed",
    "check_looks_like_sql",
    "generate_boundary_id",
    "generate_canary_token",
    "get_budget_tracker",
    "get_circuit_breaker",
    "get_guardrail_settings",
    "guardrail_sap_output",
    "guardrail_sql_output",
    "is_transient_error",
    "mask_secrets_in_text",
    "mask_sql_in_text",
    "neutralize_delimiters",
    "post_flight_pipeline",
    "pre_flight_pipeline",
    "redact_secrets",
    "sanitize_input_characters",
    "sanitize_text",
    "scan_canary_leak",
    "scan_prompt_injection",
    "structlog_guardrail_processor",
    "validate_canary_and_secrets",
    "validate_error_subset_of_opportunity",
    "validate_generated_sql",
    "validate_glossary_membership",
    "validate_literal_grounding",
    "validate_risk_consistency_floor",
    "validate_tool_arguments",
    "validate_tool_call_permission",
]
