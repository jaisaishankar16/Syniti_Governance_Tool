"""Configuration settings for LLM and OpenAI Agents SDK."""

from __future__ import annotations

import functools
import json
import logging
import os
import warnings
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.agents.settings")


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


def read_dotenv_file(env_path: Path) -> dict[str, str]:
    """Reads key-value pairs from a dotenv file without executing or exposing secrets."""
    if not env_path.is_file():
        return {}
    res: dict[str, str] = {}
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    res[k.strip()] = v.strip().strip("'\"")
    except OSError:
        return {}
    return res


def load_dotenv_key(env_path: Path) -> str | None:
    """Reads OPENAI_API_KEY or LLM_API_KEY directly from a dotenv file, filtering placeholders."""
    file_dict = read_dotenv_file(env_path)
    k = file_dict.get("OPENAI_API_KEY") or file_dict.get("LLM_API_KEY")
    return clean_api_key(k)


def clean_base_url(val: object) -> str | None:
    """Cleans raw base URL, stripping whitespace and returning None if blank."""
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def resolve_canonical_or_alias(
    canonical_name: str,
    alias_name: str,
    data: dict[str, Any],
    target_env_file: Path,
    cleaner_fn: Any,
) -> tuple[Any, str | None, str | None]:
    """Resolves value, reporting variable name, and source for a setting with a deprecated alias.

    Rule 1: If both canonical and alias are set with DIFFERENT values (in kwargs, in os.environ,
    in .env file, or across effective sources), fails fast with a message naming the two variables
    (never their values).
    Rule 2: If both are set with the SAME value, uses canonical without error.
    Rule 3: If only the alias is set, logs a one-line deprecation warning.
    Rule 4: If only canonical is set, uses canonical with no warning.
    """
    # 1. Constructor kwargs
    has_c_kw = (canonical_name in data) or (canonical_name.lower() in data)
    c_kw = (
        cleaner_fn(
            data.get(canonical_name)
            if canonical_name in data
            else data.get(canonical_name.lower())
        )
        if has_c_kw
        else None
    )

    has_a_kw = (alias_name in data) or (alias_name.lower() in data)
    a_kw = (
        cleaner_fn(
            data.get(alias_name)
            if alias_name in data
            else data.get(alias_name.lower())
        )
        if has_a_kw
        else None
    )

    if (
        has_c_kw
        and has_a_kw
        and (c_kw is not None)
        and (a_kw is not None)
        and (c_kw != a_kw)
    ):
        raise ValueError(
            f"Conflicting settings: both {canonical_name} and {alias_name} are set with different values"
        )

    # 2. OS environment
    has_c_os = canonical_name in os.environ
    c_os = cleaner_fn(os.environ.get(canonical_name)) if has_c_os else None

    has_a_os = alias_name in os.environ
    a_os = cleaner_fn(os.environ.get(alias_name)) if has_a_os else None

    if (
        has_c_os
        and has_a_os
        and (c_os is not None)
        and (a_os is not None)
        and (c_os != a_os)
    ):
        raise ValueError(
            f"Conflicting settings: both {canonical_name} and {alias_name} are set with different values"
        )

    # 3. Dotenv file
    file_dict = read_dotenv_file(target_env_file)
    has_c_file = canonical_name in file_dict
    c_file = cleaner_fn(file_dict.get(canonical_name)) if has_c_file else None

    has_a_file = alias_name in file_dict
    a_file = cleaner_fn(file_dict.get(alias_name)) if has_a_file else None

    if (
        has_c_file
        and has_a_file
        and (c_file is not None)
        and (a_file is not None)
        and (c_file != a_file)
    ):
        raise ValueError(
            f"Conflicting settings: both {canonical_name} and {alias_name} are set with different values"
        )

    # Determine effective canonical and source
    if has_c_kw:
        eff_c = c_kw
        src_c = "constructor" if c_kw is not None else None
    elif has_c_os:
        eff_c = c_os
        src_c = "OS environment" if c_os is not None else None
    elif has_c_file and c_file is not None:
        eff_c = c_file
        src_c = str(target_env_file)
    else:
        eff_c = None
        src_c = None

    # Determine effective alias and source
    if has_a_kw:
        eff_a = a_kw
        src_a = "constructor" if a_kw is not None else None
    elif has_a_os:
        eff_a = a_os
        src_a = "OS environment" if a_os is not None else None
    elif has_a_file and a_file is not None:
        eff_a = a_file
        src_a = str(target_env_file)
    else:
        eff_a = None
        src_a = None

    # Check for cross-source effective conflict
    if (eff_c is not None) and (eff_a is not None) and (eff_c != eff_a):
        raise ValueError(
            f"Conflicting settings: both {canonical_name} and {alias_name} are set with different values"
        )

    # If both are set and equal, use canonical without error
    if (eff_c is not None) and (eff_a is not None) and (eff_c == eff_a):
        return eff_c, canonical_name, src_c

    # If only alias is set, emit one-line deprecation warning
    if (eff_c is None) and (eff_a is not None):
        logger.warning(f"{alias_name} is deprecated; use {canonical_name} instead.")
        warnings.warn(
            f"{alias_name} is deprecated; use {canonical_name} instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return eff_a, f"{alias_name} (deprecated)", src_a

    # If only canonical is set
    if eff_c is not None:
        return eff_c, canonical_name, src_c

    return None, None, None


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
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, validation_alias="OPENAI_BASE_URL")

    key_variable: str | None = Field(default=None, exclude=True)
    key_source: str | None = Field(default=None, exclude=True)
    base_url_variable: str | None = Field(default=None, exclude=True)
    base_url_source: str | None = Field(default=None, exclude=True)

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

    def __init__(
        self,
        _env_file: Path | str | None = None,
        **values: Any,
    ) -> None:
        target_env = Path(_env_file) if _env_file is not None else ENV_FILE_PATH

        # Normalize passed kwargs so explicit constructor arguments override .env
        for k in list(values.keys()):
            upper_k = k.upper()
            if upper_k != k and upper_k not in values:
                values[upper_k] = values[k]

        # Parse extra headers from JSON if passed as string
        extra_h = values.get("LLM_EXTRA_HEADERS") or values.get("llm_extra_headers")
        if isinstance(extra_h, str) and extra_h.strip():
            try:
                values["llm_extra_headers"] = json.loads(extra_h)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in LLM_EXTRA_HEADERS: {exc}") from exc

        # 1. Resolve API key
        api_key_val, key_var, key_src = resolve_canonical_or_alias(
            canonical_name="OPENAI_API_KEY",
            alias_name="LLM_API_KEY",
            data=values,
            target_env_file=target_env,
            cleaner_fn=clean_api_key,
        )

        # 2. Resolve Base URL
        base_url_val, url_var, url_src = resolve_canonical_or_alias(
            canonical_name="OPENAI_BASE_URL",
            alias_name="LLM_BASE_URL",
            data=values,
            target_env_file=target_env,
            cleaner_fn=clean_base_url,
        )

        # Remove aliases from values so Pydantic doesn't see unexpected kwargs
        for k in (
            "llm_api_key",
            "LLM_API_KEY",
            "llm_base_url",
            "LLM_BASE_URL",
        ):
            values.pop(k, None)

        values["openai_api_key"] = api_key_val
        values["openai_base_url"] = base_url_val
        values["key_variable"] = key_var
        values["key_source"] = key_src
        values["base_url_variable"] = url_var
        values["base_url_source"] = url_src

        # When base URL is set and LLM_API_MODE is not specified, default to chat_completions
        api_mode = values.get("LLM_API_MODE") or values.get("llm_api_mode")
        if base_url_val and not api_mode:
            values["llm_api_mode"] = "chat_completions"

        super().__init__(_env_file=target_env, **values)

        # Force resolved canonical values and sources so Pydantic's dotenv source
        # cannot resurrect keys when an explicit placeholder or empty key was provided
        object.__setattr__(
            self,
            "openai_api_key",
            SecretStr(api_key_val) if api_key_val else None,
        )
        object.__setattr__(self, "openai_base_url", base_url_val)
        object.__setattr__(self, "key_variable", key_var)
        object.__setattr__(self, "key_source", key_src)
        object.__setattr__(self, "base_url_variable", url_var)
        object.__setattr__(self, "base_url_source", url_src)

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

    active_key = (
        active_settings.openai_api_key.get_secret_value()
        if active_settings.openai_api_key
        else None
    )

    key_len = len(active_key) if active_key else 0

    return {
        "env_path": ENV_FILE_PATH,
        "env_exists": env_exists,
        "env_is_empty": env_is_empty,
        "key_variable": active_settings.key_variable,
        "key_source": active_settings.key_source,
        "key_length": key_len,
        "base_url_variable": active_settings.base_url_variable,
        "base_url_source": active_settings.base_url_source,
        "configured": active_key is not None,
        "has_valid_key": active_key is not None,
    }


@functools.lru_cache(maxsize=1)
def get_llm_settings() -> LLMSettings:
    """Singleton getter for LLMSettings."""
    return LLMSettings()
