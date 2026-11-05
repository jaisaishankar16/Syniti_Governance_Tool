"""FastAPI dependencies for settings, authentication, repositories, and orchestrator."""

from __future__ import annotations

import hmac
from typing import Any

from app.api.models import Principal
from app.api.repositories import (
    ActivityRepository,
    DefaultProjectRepository,
    JsonLinesActivityRepository,
    ProjectRepository,
)
from app.api.settings import ApiSettings, get_api_settings
from fastapi import Depends, Header, HTTPException, Request, status


def compute_initials(name: str) -> str:
    """Computes two-character initials from a display name."""
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][0]}{parts[-1][0]}".upper()


def get_settings(request: Request) -> ApiSettings:
    """Dependency returning application ApiSettings from app state or cached config."""
    state_settings = getattr(request.app.state, "settings", None)
    if state_settings is not None:
        return state_settings
    return get_api_settings()


async def get_principal(
    request: Request,
    settings: ApiSettings = Depends(get_settings),
) -> Principal:
    """Pluggable dependency returning the authenticated user identity.

    Honors X-User-Email header if provided, falling back to configured placeholder principal.
    """
    name = settings.placeholder_principal_name
    email = request.headers.get("X-User-Email", settings.placeholder_principal_email)
    return Principal(
        id="user-principal",
        display_name=name,
        email=email,
        roles=["admin", "analyst"],
        initials=compute_initials(name),
    )


async def verify_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    settings: ApiSettings = Depends(get_settings),
) -> str:
    """Verifies administrator API key using constant-time comparison.

    Rules:
      - If ADMIN_API_KEY is unset, all admin endpoints return HTTP 404 (disabled).
      - If key is provided and valid, returns the verified key.
      - If key is missing or invalid, returns HTTP 403 Forbidden.
    """
    if settings.admin_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin API is not configured on this deployment",
        )

    expected_key = settings.admin_api_key.get_secret_value()
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin API is not configured on this deployment",
        )

    if not x_admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required X-Admin-Key header",
        )

    if not hmac.compare_digest(x_admin_key.strip(), expected_key.strip()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid administrative API key",
        )

    return x_admin_key


def get_project_repo(request: Request) -> ProjectRepository:
    """Dependency retrieving the active ProjectRepository singleton."""
    repo = getattr(request.app.state, "project_repo", None)
    if repo is None:
        repo = DefaultProjectRepository()
        request.app.state.project_repo = repo
    return repo  # type: ignore[no-any-return]


def get_activity_repo(
    request: Request,
    settings: ApiSettings = Depends(get_settings),
) -> ActivityRepository:
    """Dependency retrieving the active ActivityRepository singleton."""
    repo = getattr(request.app.state, "activity_repo", None)
    if repo is None:
        activity_file = settings.activity_data_dir / "activity.jsonl"
        repo = JsonLinesActivityRepository(file_path=activity_file)
        request.app.state.activity_repo = repo
    return repo  # type: ignore[no-any-return]


def get_orchestrator(request: Request) -> Any:
    """Dependency retrieving the orchestrator analysis function or double."""
    return getattr(request.app.state, "orchestrator", None)
