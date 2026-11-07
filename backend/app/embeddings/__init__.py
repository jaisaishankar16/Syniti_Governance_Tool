"""OpenAI-compatible embeddings package for knowledge retrieval and semantic caching."""

from app.embeddings.client import (
    create_embedding_async_client,
    create_embedding_sync_client,
)
from app.embeddings.embedder import (
    Embedder,
    OpenAIEmbedder,
    classify_embedding_error,
    estimate_tokens,
    get_embedder,
    set_global_embedder,
)
from app.embeddings.models import (
    CalibrationFile,
    EmbeddingResponseError,
    EmbeddingResult,
    EmbeddingUsage,
    ModelCalibration,
    RetrievalEvalQuery,
)
from app.embeddings.settings import (
    EmbeddingSettings,
    get_embedding_settings,
    reset_embedding_settings,
)

__all__ = [
    "CalibrationFile",
    "Embedder",
    "EmbeddingResponseError",
    "EmbeddingResult",
    "EmbeddingSettings",
    "EmbeddingUsage",
    "ModelCalibration",
    "OpenAIEmbedder",
    "RetrievalEvalQuery",
    "classify_embedding_error",
    "create_embedding_async_client",
    "create_embedding_sync_client",
    "estimate_tokens",
    "get_embedder",
    "get_embedding_settings",
    "reset_embedding_settings",
    "set_global_embedder",
]
