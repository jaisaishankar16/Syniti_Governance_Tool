"""Governance glossary helper for classification options and proposed learned terms."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from filelock import FileLock

from app.knowledge.models import GlossaryConceptGroup, GovernanceGlossaryFile
from app.knowledge.settings import KnowledgeSettings, get_settings


class GlossaryHelper:
    """Manages reading the official governance glossary and recording proposed terms."""

    def __init__(self, settings: KnowledgeSettings | None = None) -> None:
        self.settings = settings or get_settings()
        self.glossary_path = self.settings.governance_glossary_path
        self.learned_glossary_path = self.settings.learned_glossary_path
        self._lock = threading.Lock()
        self._data: GovernanceGlossaryFile | None = None
        self._load()

    def _load(self) -> None:
        if not self.glossary_path.exists():
            return
        with open(self.glossary_path, "r", encoding="utf-8") as f:
            self._data = GovernanceGlossaryFile.model_validate_json(f.read())

    def list_options(self, concept_group: str) -> list[str]:
        """Lists valid options/keys/codes for a given governance concept group."""
        if self._data is None:
            self._load()
        if self._data is None:
            return []

        group_lower = concept_group.strip().lower()

        # Normalize singular/plural
        normalized = group_lower
        if not hasattr(self._data, normalized):
            if normalized.endswith("s") and hasattr(self._data, normalized[:-1]):
                normalized = normalized[:-1]
            elif hasattr(self._data, normalized + "s"):
                normalized = normalized + "s"

        if hasattr(self._data, normalized):
            group: GlossaryConceptGroup = getattr(self._data, normalized)
            return [group.name] + list(group.aliases) + list(group.examples)

        return []

    def propose_entry(self, concept_group: str, entry: dict[str, Any]) -> dict[str, Any]:
        """Proposes a new glossary entry into learned_glossary.json with status='proposed'."""
        with self._lock:
            lock_path = self.learned_glossary_path.with_suffix(".lock")
            with FileLock(str(lock_path), timeout=10):
                existing_data: dict[str, Any] = {
                    "schema_version": 1,
                    "proposed_entries": [],
                }
                if self.learned_glossary_path.exists():
                    try:
                        with open(self.learned_glossary_path, "r", encoding="utf-8") as f:
                            existing_data = json.load(f)
                    except (OSError, json.JSONDecodeError):
                        existing_data = {"schema_version": 1, "proposed_entries": []}

                proposed_record = dict(entry)
                proposed_record["concept_group"] = concept_group
                proposed_record["status"] = "proposed"
                proposed_record["proposed_at"] = datetime.now(timezone.utc).isoformat()

                entries: list[dict[str, Any]] = existing_data.setdefault("proposed_entries", [])
                entries.append(proposed_record)

                tmp_path = self.learned_glossary_path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, indent=2)
                os.replace(tmp_path, self.learned_glossary_path)

                return proposed_record

    def get_proposals(self) -> list[dict[str, Any]]:
        """Returns all proposed glossary entries from learned_glossary.json."""
        with self._lock:
            if not self.learned_glossary_path.exists():
                return []
            try:
                with open(self.learned_glossary_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return list(data.get("proposed_entries", []))
            except (OSError, json.JSONDecodeError):
                return []
