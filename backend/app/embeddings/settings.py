"""Configuration settings for OpenAI-compatible embeddings via pydantic-settings."""

from __future__ import annotations

import hashlib
import logging
import os
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.agents.settings import clean_api_key, clean_base_url, is_valid_api_key, read_dotenv_file

logger = logging.getLogger("app.embeddings.settings")


def find_repo_root() -> Path:
    """Finds repository root anchoring on backend/app/embeddings/settings.py."""
    return Path(__file__).resolve().parents[3]


REPO_ROOT = find_repo_root()
ENV_FILE_PATH = REPO_ROOT / ".env"


class EmbeddingSettings(BaseSettings):
    """OpenAI-compatible embeddings settings loaded from environment or .env."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias="EMBEDDING_MODEL",
    )
    embedding_dimensions: int | None = Field(
        default=None,
        validation_alias="EMBEDDING_DIMENSIONS",
    )
    embedding_base_url: str | None = Field(
        default=None,
        validation_alias="EMBEDDING_BASE_URL",
    )
    embedding_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="EMBEDDING_API_KEY",
    )
    embedding_batch_size: int = Field(
        default=96,
        validation_alias="EMBEDDING_BATCH_SIZE",
    )
    embedding_max_concurrency: int = Field(
        default=4,
        validation_alias="EMBEDDING_MAX_CONCURRENCY",
    )
    embedding_timeout_s: float = Field(
        default=30.0,
        validation_alias="EMBEDDING_TIMEOUT_S",
    )
    embedding_max_calls_per_request: int = Field(
        default=12,
        validation_alias="EMBEDDING_MAX_CALLS_PER_REQUEST",
    )
    embedding_price_per_m_tokens: float | None = Field(
        default=None,
        validation_alias="EMBEDDING_PRICE_PER_M_TOKENS",
    )
    embedding_query_cache_size: int = Field(
        default=2000,
        validation_alias="EMBEDDING_QUERY_CACHE_SIZE",
    )

    # Resolution tracking
    effective_base_url: str | None = None
    effective_api_key: SecretStr | None = None
    has_separate_credentials: bool = False
    key_variable: str | None = None
    key_source: str | None = None
    key_length: int = 0
    base_url_variable: str | None = None
    base_url_source: str | None = None

    @field_validator("embedding_dimensions", mode="before")
    @classmethod
    def parse_dimensions(cls, val: Any) -> int | None:
        if val is None or val == "" or str(val).strip() == "":
            return None
        return int(val)

    @field_validator("embedding_price_per_m_tokens", mode="before")
    @classmethod
    def parse_price(cls, val: Any) -> float | None:
        if val is None or val == "" or str(val).strip() == "":
            return None
        return float(val)

    @field_validator("embedding_base_url", mode="before")
    @classmethod
    def parse_base_url(cls, val: Any) -> str | None:
        return clean_base_url(val)

    @field_validator("embedding_api_key", mode="before")
    @classmethod
    def parse_api_key(cls, val: Any) -> SecretStr | None:
        cleaned = clean_api_key(val)
        return SecretStr(cleaned) if cleaned else None

    @model_validator(mode="before")
    @classmethod
    def check_deprecated_know_embedding_model(cls, data: Any) -> Any:
        # Check for deprecated KNOW_EMBEDDING_MODEL
        know_val = None
        if isinstance(data, dict):
            know_val = data.get("KNOW_EMBEDDING_MODEL") or data.get("know_embedding_model")
        if know_val is None:
            know_val = os.environ.get("KNOW_EMBEDDING_MODEL")
        if know_val is None and ENV_FILE_PATH.is_file():
            file_dict = read_dotenv_file(ENV_FILE_PATH)
            know_val = file_dict.get("KNOW_EMBEDDING_MODEL")

        if know_val:
            warnings.warn(
                "KNOW_EMBEDDING_MODEL is deprecated and ignored; use EMBEDDING_MODEL instead",
                DeprecationWarning,
                stacklevel=2,
            )
            logger.warning("KNOW_EMBEDDING_MODEL is deprecated and ignored; use EMBEDDING_MODEL instead")
        return data

    @model_validator(mode="after")
    def resolve_effective_endpoints_and_credentials(self) -> EmbeddingSettings:
        # Check if separate embedding credentials were provided
        has_sep_key = self.embedding_api_key is not None
        has_sep_url = self.embedding_base_url is not None
        self.has_separate_credentials = has_sep_key or has_sep_url

        # Resolve Base URL
        if self.embedding_base_url:
            self.effective_base_url = self.embedding_base_url
            self.base_url_variable = "EMBEDDING_BASE_URL"
            self.base_url_source = "constructor" if "embedding_base_url" in self.model_fields_set else "environment"
        else:
            # Fallback to OPENAI_BASE_URL or LLM_BASE_URL from env or .env
            env_url = clean_base_url(os.environ.get("OPENAI_BASE_URL") or os.environ.get("LLM_BASE_URL"))
            if env_url:
                self.effective_base_url = env_url
                self.base_url_variable = "OPENAI_BASE_URL" if "OPENAI_BASE_URL" in os.environ else "LLM_BASE_URL"
                self.base_url_source = "OS environment"
            else:
                file_dict = read_dotenv_file(ENV_FILE_PATH)
                file_url = clean_base_url(file_dict.get("OPENAI_BASE_URL") or file_dict.get("LLM_BASE_URL"))
                if file_url:
                    self.effective_base_url = file_url
                    self.base_url_variable = "OPENAI_BASE_URL" if "OPENAI_BASE_URL" in file_dict else "LLM_BASE_URL"
                    self.base_url_source = str(ENV_FILE_PATH)
                else:
                    self.effective_base_url = None
                    self.base_url_variable = None
                    self.base_url_source = "default"

        # Resolve API Key
        if self.embedding_api_key:
            self.effective_api_key = self.embedding_api_key
            self.key_variable = "EMBEDDING_API_KEY"
            self.key_source = "constructor" if "embedding_api_key" in self.model_fields_set else "environment"
        else:
            # Fallback to OPENAI_API_KEY or LLM_API_KEY
            env_key = clean_api_key(os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY"))
            if env_key:
                self.effective_api_key = SecretStr(env_key)
                self.key_variable = "OPENAI_API_KEY" if "OPENAI_API_KEY" in os.environ else "LLM_API_KEY"
                self.key_source = "OS environment"
            else:
                file_dict = read_dotenv_file(ENV_FILE_PATH)
                file_key = clean_api_key(file_dict.get("OPENAI_API_KEY") or file_dict.get("LLM_API_KEY"))
                if file_key:
                    self.effective_api_key = SecretStr(file_key)
                    self.key_variable = "OPENAI_API_KEY" if "OPENAI_API_KEY" in file_dict else "LLM_API_KEY"
                    self.key_source = str(ENV_FILE_PATH)
                else:
                    self.effective_api_key = None
                    self.key_variable = None
                    self.key_source = None

        if self.effective_api_key:
            raw = self.effective_api_key.get_secret_value()
            self.key_length = len(raw) if is_valid_api_key(raw) else 0
        else:
            self.key_length = 0

        return self

    @property
    def is_configured(self) -> bool:
        """Returns True if a valid, non-placeholder API key is available."""
        if self.effective_api_key is None:
            return False
        return is_valid_api_key(self.effective_api_key.get_secret_value())

    @property
    def base_host(self) -> str:
        """Returns hostname of the effective base URL or 'api.openai.com'."""
        if not self.effective_base_url:
            return "api.openai.com"
        parsed = urlparse(self.effective_base_url)
        return parsed.netloc or parsed.path or "api.openai.com"

    @property
    def base_url_hash(self) -> str:
        """Computes a stable SHA-256 hash of the provider host."""
        host = self.base_host.lower().strip()
        return hashlib.sha256(host.encode("utf-8")).hexdigest()[:16]

    @property
    def provider_type(self) -> str:
        """Returns 'gateway' if effective base URL is custom, or 'direct'."""
        if self.effective_base_url and "api.openai.com" not in self.effective_base_url:
            return "gateway"
        return "direct"


_embedding_settings_instance: EmbeddingSettings | None = None


def get_embedding_settings() -> EmbeddingSettings:
    """Returns singleton instance of EmbeddingSettings."""
    global _embedding_settings_instance
    if _embedding_settings_instance is None:
        _embedding_settings_instance = EmbeddingSettings()
    return _embedding_settings_instance


def reset_embedding_settings() -> None:
    """Resets singleton instance (useful in unit tests)."""
    global _embedding_settings_instance
    _embedding_settings_instance = None
