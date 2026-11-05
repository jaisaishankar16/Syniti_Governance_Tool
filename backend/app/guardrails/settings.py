"""Configuration settings for the Guardrails safety layer via pydantic-settings."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repo_root() -> Path:
    """Finds repository root anchoring on backend/app/guardrails/settings.py."""
    return Path(__file__).resolve().parents[3]


REPO_ROOT = find_repo_root()
ENV_FILE_PATH = REPO_ROOT / ".env"
KNOWLEDGE_DIR = REPO_ROOT / "knowledge" / "guardrails"


class GuardrailSettings(BaseSettings):
    """Configuration for all safety, security, budget, and resilience guardrails."""

    model_config = SettingsConfigDict(
        env_prefix="GUARD_",
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # Operational modes: "off" | "warn" | "enforce"
    guard_mode_input_size: Literal["off", "warn", "enforce"] = Field(
        default="enforce", validation_alias="GUARD_MODE_INPUT_SIZE"
    )
    guard_mode_looks_like_sql: Literal["off", "warn", "enforce"] = Field(
        default="enforce", validation_alias="GUARD_MODE_LOOKS_LIKE_SQL"
    )
    guard_mode_secrets: Literal["off", "warn", "enforce"] = Field(
        default="enforce", validation_alias="GUARD_MODE_SECRETS"
    )
    guard_mode_pii: Literal["off", "warn", "redact", "block"] = Field(
        default="warn", validation_alias="GUARD_MODE_PII"
    )
    guard_mode_injection: Literal["off", "warn", "sanitize", "block"] = Field(
        default="sanitize", validation_alias="GUARD_MODE_INJECTION"
    )
    guard_mode_tool_policy: Literal["off", "warn", "enforce"] = Field(
        default="enforce", validation_alias="GUARD_MODE_TOOL_POLICY"
    )
    guard_mode_agent_output: Literal["off", "warn", "enforce"] = Field(
        default="enforce", validation_alias="GUARD_MODE_AGENT_OUTPUT"
    )
    guard_mode_final_output: Literal["off", "warn", "enforce"] = Field(
        default="enforce", validation_alias="GUARD_MODE_FINAL_OUTPUT"
    )

    # Thresholds
    max_sql_chars: int = Field(default=50_000, validation_alias="MAX_SQL_CHARS")
    max_statements: int = Field(default=10, validation_alias="MAX_STATEMENTS")
    max_nesting: int = Field(default=15, validation_alias="MAX_NESTING")
    max_token_len: int = Field(default=256, validation_alias="MAX_TOKEN_LEN")
    max_field_chars: int = Field(default=5_000, validation_alias="MAX_FIELD_CHARS")
    max_tool_query_chars: int = Field(default=256, validation_alias="MAX_TOOL_QUERY_CHARS")
    max_writes_per_run: int = Field(default=3, validation_alias="MAX_WRITES_PER_RUN")
    per_request_token_budget: int = Field(
        default=50_000, validation_alias="PER_REQUEST_TOKEN_BUDGET"
    )
    daily_token_budget: int = Field(default=1_000_000, validation_alias="DAILY_TOKEN_BUDGET")
    max_concurrent_runs: int = Field(default=10, validation_alias="MAX_CONCURRENT_RUNS")

    # Feature flags & policies
    learned_requires_approval: bool = Field(
        default=True, validation_alias="LEARNED_REQUIRES_APPROVAL"
    )
    llm_guard_enabled: bool = Field(default=False, validation_alias="LLM_GUARD_ENABLED")
    circuit_failure_threshold: int = Field(default=3, validation_alias="CIRCUIT_FAILURE_THRESHOLD")
    circuit_reset_s: float = Field(default=30.0, validation_alias="CIRCUIT_RESET_S")
    debug_log_sql: bool = Field(default=False, validation_alias="DEBUG_LOG_SQL")
    policy_version: str = "v1.0"

    # Pattern paths
    injection_patterns_path: Path = KNOWLEDGE_DIR / "injection_patterns.json"
    secret_patterns_path: Path = KNOWLEDGE_DIR / "secret_patterns.json"
    pii_patterns_path: Path = KNOWLEDGE_DIR / "pii_patterns.json"

    # Cost calculation table (user-filled, empty by default)
    pricing_table: dict[str, dict[str, float]] = Field(
        default_factory=dict, validation_alias="PRICING_TABLE"
    )


@functools.lru_cache(maxsize=1)
def get_guardrail_settings() -> GuardrailSettings:
    """Singleton getter for GuardrailSettings."""
    return GuardrailSettings()
