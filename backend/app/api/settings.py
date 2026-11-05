"""Configuration settings for the FastAPI application layer."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repo_root() -> Path:
    """Finds repository root anchoring on backend/app/api/settings.py."""
    return Path(__file__).resolve().parents[3]


REPO_ROOT = find_repo_root()
ENV_FILE_PATH = REPO_ROOT / ".env"


class ApiSettings(BaseSettings):
    """FastAPI configuration settings loaded from environment variables and root .env."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_env: Literal["development", "production"] = Field(
        default="development", validation_alias="APP_ENV"
    )
    host: str = Field(default="127.0.0.1", validation_alias="HOST")
    port: int = Field(default=8000, validation_alias="PORT")

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        validation_alias="CORS_ORIGINS",
    )

    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["*"], validation_alias="TRUSTED_HOSTS"
    )
    trusted_proxies: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "::1", "localhost"],
        validation_alias="TRUSTED_PROXIES",
    )

    max_body_bytes: int = Field(default=1_048_576, validation_alias="MAX_BODY_BYTES")
    request_timeout_s: float = Field(default=120.0, validation_alias="REQUEST_TIMEOUT_S")
    sse_heartbeat_s: float = Field(default=15.0, validation_alias="SSE_HEARTBEAT_S")

    rate_limit_analyze: str = Field(default="20/minute", validation_alias="RATE_LIMIT_ANALYZE")
    rate_limit_default: str = Field(default="120/minute", validation_alias="RATE_LIMIT_DEFAULT")
    rate_limit_backend: Literal["memory", "redis"] = Field(
        default="memory", validation_alias="RATE_LIMIT_BACKEND"
    )

    admin_api_key: SecretStr | None = Field(default=None, validation_alias="ADMIN_API_KEY")

    docs_enabled: bool | None = Field(default=None, validation_alias="DOCS_ENABLED")
    metrics_enabled: bool = Field(default=True, validation_alias="METRICS_ENABLED")
    metrics_require_admin: bool | None = Field(
        default=None, validation_alias="METRICS_REQUIRE_ADMIN"
    )

    shutdown_grace_s: float = Field(default=30.0, validation_alias="SHUTDOWN_GRACE_S")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_json: bool | None = Field(default=None, validation_alias="LOG_JSON")
    debug_log_sql: bool = Field(default=False, validation_alias="DEBUG_LOG_SQL")
    allow_force_refresh: bool = Field(default=True, validation_alias="ALLOW_FORCE_REFRESH")

    placeholder_principal_name: str = Field(
        default="Jai Shankar", validation_alias="PLACEHOLDER_PRINCIPAL_NAME"
    )
    placeholder_principal_email: str = Field(
        default="jai.shankar@example.com", validation_alias="PLACEHOLDER_PRINCIPAL_EMAIL"
    )
    activity_data_dir: Path = Field(
        default_factory=lambda: REPO_ROOT / "data", validation_alias="ACTIVITY_DATA_DIR"
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            import json

            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed]
            except json.JSONDecodeError:
                return [orig.strip() for orig in v.split(",") if orig.strip()]
        return v

    @field_validator("trusted_hosts", "trusted_proxies", mode="before")
    @classmethod
    def _parse_comma_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            import json

            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed]
            except json.JSONDecodeError:
                return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _apply_environment_defaults(self) -> ApiSettings:
        # Reject wildcard CORS when credentials are used
        if "*" in self.cors_origins and len(self.cors_origins) > 1:
            raise ValueError(
                "CORS origins cannot contain wildcard '*' when explicit origins are specified"
            )

        # Computed defaults based on app_env
        is_prod = self.app_env == "production"

        if self.docs_enabled is None:
            self.docs_enabled = not is_prod

        if self.metrics_require_admin is None:
            self.metrics_require_admin = is_prod

        if self.log_json is None:
            self.log_json = is_prod

        return self


@functools.lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    """Returns singleton cached ApiSettings."""
    return ApiSettings()
