"""Pydantic v2 data models for cached agent outcomes and synthesized intelligence."""

from __future__ import annotations

import hashlib
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ProducedBy(BaseModel):
    """Metadata recording the exact generator provenance of a cached entry."""

    model_config = ConfigDict(extra="forbid")

    model: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    knowledge_fingerprint: str = ""


class CacheEntry(BaseModel, Generic[T]):
    """Versioned, integrity-verified wrapper around any cached object."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    key: str
    kind: Literal["L1_final", "L2_sql", "L2_sap", "L3_similarity"]
    created_at: str
    expires_at: str
    hit_count: int = 0
    payload: T
    payload_sha256: str
    produced_by: ProducedBy

    @classmethod
    def create(
        cls,
        key: str,
        kind: Literal["L1_final", "L2_sql", "L2_sap", "L3_similarity"],
        payload: T,
        created_at: str,
        expires_at: str,
        produced_by: ProducedBy,
    ) -> CacheEntry[T]:
        """Factory creating an entry with an automatic payload sha256 checksum."""
        if hasattr(payload, "model_dump_json"):
            payload_str = payload.model_dump_json()  # type: ignore[union-attr]
        elif isinstance(payload, str):
            payload_str = payload
        else:
            payload_str = str(payload)

        sha = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

        return cls(
            key=key,
            kind=kind,
            created_at=created_at,
            expires_at=expires_at,
            payload=payload,
            payload_sha256=sha,
            produced_by=produced_by,
        )

    def verify_integrity(self) -> bool:
        """Verifies that the payload matches its cryptographic sha256 hash.

        Returns False if corrupted or tampered (cache integrity guardrail).
        """
        if hasattr(self.payload, "model_dump_json"):
            payload_str = self.payload.model_dump_json()  # type: ignore[union-attr]
        elif isinstance(self.payload, str):
            payload_str = self.payload
        else:
            payload_str = str(self.payload)

        computed = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
        return computed == self.payload_sha256
