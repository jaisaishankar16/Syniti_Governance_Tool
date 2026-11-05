"""Repository interfaces and persistence implementations for projects and activity execution records."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import anyio
from app.api.models import ActivityItem, Project
from filelock import FileLock
from pydantic import TypeAdapter

logger = logging.getLogger(__name__)


def find_repo_root() -> Path:
    """Finds repository root anchoring on backend/app/api/repositories.py."""
    return Path(__file__).resolve().parents[3]


REPO_ROOT = find_repo_root()


class ProjectRepository(Protocol):
    """Protocol defining project metadata access."""

    async def list_projects(self) -> list[Project]:
        """Returns all configured projects."""
        ...

    async def get_project(self, project_id: str) -> Project | None:
        """Returns project by ID or None if not found."""
        ...


class ActivityRepository(Protocol):
    """Protocol defining recent execution activity access and recording."""

    async def list_activity(self, limit: int = 20) -> list[ActivityItem]:
        """Returns recent activity entries up to limit (newest first)."""
        ...

    async def record_activity(self, item: ActivityItem) -> None:
        """Records an execution outcome."""
        ...


class DefaultProjectRepository:
    """Default project repository returning empty list by default (ready for future data source)."""

    def __init__(self, projects: list[Project] | None = None) -> None:
        self._projects: list[Project] = list(projects or [])

    async def list_projects(self) -> list[Project]:
        return list(self._projects)

    async def get_project(self, project_id: str) -> Project | None:
        for p in self._projects:
            if p.id == project_id:
                return p
        return None


# Backward-compatible alias
JsonFileProjectRepository = DefaultProjectRepository


class JsonLinesActivityRepository:
    """Activity repository storing real execution records in an append-only JSON Lines file."""

    def __init__(
        self,
        file_path: Path | None = None,
        max_entries: int = 200,
    ) -> None:
        self.file_path = file_path or (REPO_ROOT / "data" / "activity.jsonl")
        self.lock_path = self.file_path.with_suffix(".lock")
        self.max_entries = max_entries
        self._adapter = TypeAdapter(ActivityItem)

    def _sync_record(self, item: ActivityItem) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.lock_path), timeout=5.0):
            lines: list[str] = []
            if self.file_path.exists():
                with open(self.file_path, "r", encoding="utf-8") as f:
                    lines = [ln.strip() for ln in f if ln.strip()]

            lines.append(item.model_dump_json())
            if len(lines) > self.max_entries:
                lines = lines[-self.max_entries :]

            temp_path = self.file_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                f.writelines(line + "\n" for line in lines)
            temp_path.replace(self.file_path)

    async def record_activity(self, item: ActivityItem) -> None:
        await anyio.to_thread.run_sync(self._sync_record, item)

    def _sync_list(self, limit: int) -> list[ActivityItem]:
        if not self.file_path.exists():
            return []
        items: list[ActivityItem] = []
        with FileLock(str(self.lock_path), timeout=5.0):
            with open(self.file_path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            for line in reversed(lines):
                try:
                    items.append(self._adapter.validate_json(line))
                    if len(items) >= limit:
                        break
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Skipping unparseable activity line: %s", exc)
                    continue
        return items

    async def list_activity(self, limit: int = 20) -> list[ActivityItem]:
        return await anyio.to_thread.run_sync(self._sync_list, limit)


# Backward-compatible alias
JsonFileActivityRepository = JsonLinesActivityRepository


class InMemoryProjectRepository:
    """In-memory test double for ProjectRepository."""

    def __init__(self, projects: list[Project] | None = None) -> None:
        self.projects = projects or []

    async def list_projects(self) -> list[Project]:
        return list(self.projects)

    async def get_project(self, project_id: str) -> Project | None:
        for p in self.projects:
            if p.id == project_id:
                return p
        return None


class InMemoryActivityRepository:
    """In-memory test double for ActivityRepository."""

    def __init__(self, items: list[ActivityItem] | None = None) -> None:
        self.items = list(items or [])

    async def list_activity(self, limit: int = 20) -> list[ActivityItem]:
        return list(self.items[:limit])

    async def record_activity(self, item: ActivityItem) -> None:
        self.items.insert(0, item)
