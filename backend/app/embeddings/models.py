"""Pydantic v2 data models, validation, and error types for OpenAI-compatible embeddings."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EmbeddingUsage(BaseModel):
    """Token consumption for embedding generation."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingResult(BaseModel):
    """Validated result payload from embedding generation."""

    model_config = ConfigDict(extra="forbid")

    vectors: list[list[float]]
    model: str
    dimensions: int
    usage: EmbeddingUsage = Field(default_factory=EmbeddingUsage)
    request_count: int = 1
    truncated_count: int = 0
    latency_ms: float = 0.0

    @field_validator("vectors")
    @classmethod
    def validate_vectors(cls, v: list[list[float]]) -> list[list[float]]:
        for i, vec in enumerate(v):
            if not vec:
                raise ValueError(f"Vector at index {i} is empty")
            for j, val in enumerate(vec):
                if math.isnan(val) or math.isinf(val):
                    raise ValueError(f"Vector at index {i} has invalid value (NaN/Inf) at index {j}")
        return v

    @model_validator(mode="after")
    def validate_and_normalize(self) -> EmbeddingResult:
        normalized_vectors: list[list[float]] = []
        for i, vec in enumerate(self.vectors):
            if len(vec) != self.dimensions:
                raise ValueError(
                    f"Vector at index {i} has dimension {len(vec)}, expected {self.dimensions}"
                )
            arr = np.array(vec, dtype=np.float32)
            norm = float(np.linalg.norm(arr))
            if norm > 0.0:
                arr = arr / norm
            normalized_vectors.append(arr.tolist())
        self.vectors = normalized_vectors
        return self


class EmbeddingResponseError(Exception):
    """Typed exception raised when embedding provider returns an error or invalid payload."""

    def __init__(
        self,
        message: str,
        code: str = "EMBEDDING_UNAVAILABLE",
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.retryable = retryable

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ModelCalibration(BaseModel):
    """Pydantic-validated calibration thresholds and metrics for a specific embedding model configuration."""

    model_config = ConfigDict(extra="forbid")

    sap_min_score: float
    sql_min_score: float
    l3_hint_threshold: float
    l3_reuse_threshold: float | None = None
    write_back_min_score: float
    created_at: str
    metrics: dict[str, Any] = Field(default_factory=dict)


class CalibrationFile(BaseModel):
    """Root model for knowledge/calibration/embedding_calibration.json."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    models: dict[str, ModelCalibration] = Field(default_factory=dict)


class RetrievalEvalQuery(BaseModel):
    """Labelled query used for evaluating embedding retrieval accuracy."""

    model_config = ConfigDict(extra="forbid")

    query: str
    domain: str  # "sap" or "sql"
    expected_target: str
    is_negative: bool = False
    verified: bool = True
