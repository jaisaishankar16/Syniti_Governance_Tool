"""Configuration settings for LLM and OpenAI Agents SDK."""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repo_root() -> Path:
    """Finds repository root anchoring on backend/app/agents/settings.py."""
    return Path(__file__).resolve().parents[3]


REPO_ROOT = find_repo_root()
ENV_FILE_PATH = REPO_ROOT / ".env"

PLACEHOLDER_KEYS = {
    "",
    "api_key",
    "your-key-here",
    "changeme",
    "dummy",
    "none",
    "null",
    "sk-...",
    "sk-xxx",
    "placeholder",
}


def clean_api_key(val: object) -> str | None:
    """Cleans raw API key, treating blank values and obvious placeholders as None."""
    if val is None:
        return None
    s = val.get_secret_value() if isinstance(val, SecretStr) else str(val)
    s = s.strip()
    if not s:
        return None
    low = s.lower()
    if low in PLACEHOLDER_KEYS:
        return None
    if (
        s.startswith("<")
        or low.startswith("sk-...")
        or "your-key" in low
        or "your_key" in low
        or "changeme" in low
        or "placeholder" in low
        or "dummy" in low
    ):
        return None
    return s


def is_valid_api_key(val: object) -> bool:
    """Returns True if the value is a non-empty, non-placeholder API key."""
    return clean_api_key(val) is not None


def load_dotenv_key(env_path: Path) -> str | None:
    """Reads OPENAI_API_KEY or LLM_API_KEY directly from a dotenv file, filtering placeholders."""
    if not env_path.is_file():
        return None
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() in ("OPENAI_API_KEY", "LLM_API_KEY"):
                        clean_v = v.strip().strip("'\"")
                        res = clean_api_key(clean_v)
                        if res:
                            return res
    except OSError:
        return None
    return None


class LLMSettings(BaseSettings):
    """Settings for LLM agents, models, API modes, and operational limits."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    llm_model: str = Field(default="gpt-4o-mini", validation_alias="LLM_MODEL")
    openai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "LLM_API_KEY")
    )
    openai_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_BASE_URL", "LLM_BASE_URL")
    )

    llm_api_mode: Literal["responses", "chat_completions"] = Field(
        default="responses", validation_alias="LLM_API_MODE"
    )
    llm_temperature: float = Field(default=0.1, validation_alias="LLM_TEMPERATURE")
    llm_max_turns: int = Field(default=6, validation_alias="LLM_MAX_TURNS")
    llm_timeout_s: float = Field(default=60.0, validation_alias="LLM_TIMEOUT_S")
    llm_max_retries: int = Field(default=3, validation_alias="LLM_MAX_RETRIES")
    structured_output_mode: Literal["auto", "native", "json_prompt"] = Field(
        default="auto", validation_alias="STRUCTURED_OUTPUT_MODE"
    )
    tools_mode: Literal["auto", "native", "prefetch"] = Field(
        default="auto", validation_alias="TOOLS_MODE"
    )
    llm_ca_bundle: Path | None = Field(default=None, validation_alias="LLM_CA_BUNDLE")
    llm_extra_headers: dict[str, str] | None = Field(
        default=None, validation_alias="LLM_EXTRA_HEADERS"
    )
    agents_tracing: bool = Field(default=False, validation_alias="AGENTS_TRACING")
    learning_enabled: bool = Field(default=True, validation_alias="LEARNING_ENABLED")
    max_search_calls_per_run: int = Field(default=4, validation_alias="MAX_SEARCH_CALLS_PER_RUN")
    max_retrieved_chars: int = Field(default=800, validation_alias="MAX_RETRIEVED_CHARS")
    pack_max_items: int = Field(default=40, validation_alias="PACK_MAX_ITEMS")

    @field_validator("openai_base_url", mode="before")
    @classmethod
    def validate_url(cls, v: object) -> object:
        """Validates base URL constraints (https only, no credentials, no whitespace)."""
        if v is None:
            return None
        from app.agents.client_factory import validate_base_url

        return validate_base_url(str(v))

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def clean_key_field(cls, v: object) -> object:
        """Field validator guaranteeing that blank or placeholder keys are converted to None."""
        cleaned = clean_api_key(v)
        return cleaned

    @model_validator(mode="after")
    def validate_tracing_guard(self) -> LLMSettings:
        """Refuses AGENTS_TRACING=true when a custom base URL is configured."""
        if self.openai_base_url and self.agents_tracing:
            raise ValueError("AGENTS_TRACING cannot be enabled when OPENAI_BASE_URL is configured.")
        return self

    @model_validator(mode="before")
    @classmethod
    def resolve_settings(cls, data: dict[str, object] | object) -> dict[str, object] | object:
        """Enforces OS env > .env priority, filters placeholder keys, and configures defaults."""
        if isinstance(data, dict):
            # Parse extra headers from JSON if passed as string
            extra_h = data.get("LLM_EXTRA_HEADERS") or data.get("llm_extra_headers")
            if isinstance(extra_h, str) and extra_h.strip():
                try:
                    data["llm_extra_headers"] = json.loads(extra_h)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSON in LLM_EXTRA_HEADERS: {exc}") from exc

            # Normalize passed kwargs so explicit constructor arguments override .env
            for k in list(data.keys()):
                upper_k = k.upper()
                if upper_k != k:
                    data[upper_k] = data[k]

            # 1. API key resolution:
            has_passed_key = any(
                k in data for k in ("openai_api_key", "OPENAI_API_KEY", "llm_api_key", "LLM_API_KEY")
            )
            if has_passed_key:
                raw_passed = (
                    data.get("openai_api_key")
                    if "openai_api_key" in data
                    else (
                        data.get("llm_api_key")
                        if "llm_api_key" in data
                        else (
                            data.get("OPENAI_API_KEY")
                            if "OPENAI_API_KEY" in data
                            else data.get("LLM_API_KEY")
                        )
                    )
                )
                data.pop("OPENAI_API_KEY", None)
                data.pop("LLM_API_KEY", None)
                data["openai_api_key"] = clean_api_key(raw_passed)
            else:
                os_key = clean_api_key(
                    os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
                )
                file_key = load_dotenv_key(ENV_FILE_PATH)
                if os_key:
                    data["openai_api_key"] = os_key
                elif file_key:
                    data["openai_api_key"] = file_key
                else:
                    data["openai_api_key"] = None

            # 2. When base URL is set and LLM_API_MODE is not specified, default to chat_completions
            base_url = (
                data.get("OPENAI_BASE_URL")
                or data.get("LLM_BASE_URL")
                or data.get("openai_base_url")
                or data.get("llm_base_url")
            )
            api_mode = data.get("LLM_API_MODE") or data.get("llm_api_mode")
            if base_url and not api_mode:
                data["llm_api_mode"] = "chat_completions"

        return data


def get_key_status(settings: LLMSettings | None = None) -> dict[str, Any]:
    """Inspects environment and .env file to report key source and sanity."""
    active_settings = settings or get_llm_settings()

    env_exists = ENV_FILE_PATH.is_file()
    env_is_empty = False
    if env_exists:
        try:
            env_is_empty = (ENV_FILE_PATH.stat().st_size == 0) or (
                not ENV_FILE_PATH.read_text(encoding="utf-8").strip()
            )
        except OSError:
            env_is_empty = True

    os_clean = clean_api_key(os.environ.get("OPENAI_API_KEY"))
    file_clean = load_dotenv_key(ENV_FILE_PATH)

    active_key = (
        active_settings.openai_api_key.get_secret_value()
        if active_settings.openai_api_key
        else None
    )

    if os_clean and active_key == os_clean:
        key_source = "env var"
    elif file_clean and active_key == file_clean:
        key_source = ".env"
    elif active_key:
        key_source = "constructor"
    else:
        key_source = None

    key_len = len(active_key) if active_key else 0

    return {
        "env_path": ENV_FILE_PATH,
        "env_exists": env_exists,
        "env_is_empty": env_is_empty,
        "key_source": key_source,
        "key_length": key_len,
        "configured": active_key is not None,
        "has_valid_key": active_key is not None,
    }


@functools.lru_cache(maxsize=1)
def get_llm_settings() -> LLMSettings:
    """Singleton getter for LLMSettings."""
    return LLMSettings()
