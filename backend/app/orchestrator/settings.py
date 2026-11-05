"""Configuration settings for Orchestrator, caching, and similarity."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repo_root() -> Path:
    """Finds repository root anchoring on backend/app/orchestrator/settings.py."""
    return Path(__file__).resolve().parents[3]


REPO_ROOT = find_repo_root()
ENV_FILE_PATH = REPO_ROOT / ".env"


class OrchestratorSettings(BaseSettings):
    """Configuration settings for the end-to-end analysis orchestrator."""

    model_config = SettingsConfigDict(
        env_prefix="ORCH_",
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # Caching configuration
    cache_backend: Literal["redis", "memory", "auto"] = Field(
        default="auto", validation_alias="CACHE_BACKEND"
    )
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    cache_ttl_l1_s: int = Field(default=86400, validation_alias="CACHE_TTL_L1_S")
    cache_ttl_l2_s: int = Field(default=86400, validation_alias="CACHE_TTL_L2_S")

    # L3 Similarity settings
    similarity_reuse_threshold: float = Field(
        default=0.97, validation_alias="SIMILARITY_REUSE_THRESHOLD"
    )
    similarity_hint_threshold: float = Field(
        default=0.85, validation_alias="SIMILARITY_HINT_THRESHOLD"
    )


@functools.lru_cache(maxsize=1)
def get_orchestrator_settings() -> OrchestratorSettings:
    """Singleton getter for OrchestratorSettings."""
    return OrchestratorSettings()
