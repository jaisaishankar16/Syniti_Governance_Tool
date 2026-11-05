"""Vector index implementation using FAISS IndexFlatIP with L2 normalized embeddings."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import faiss  # type: ignore[import-untyped]
import numpy as np
from pydantic import ValidationError
from sentence_transformers import SentenceTransformer

from app.knowledge.models import DocChunk, IndexManifest, StaleIndexError
from app.knowledge.settings import KnowledgeSettings, get_settings

_MODEL_LOCK = threading.Lock()
_EMBEDDING_MODELS: dict[str, SentenceTransformer] = {}


def get_embedding_model(model_name: str) -> SentenceTransformer:
    """Thread-safe singleton getter for sentence-transformers embedding models."""
    import os

    with _MODEL_LOCK:
        if model_name not in _EMBEDDING_MODELS:
            # Respect HF_HUB_OFFLINE or try local files first when model is cached
            local_only = os.environ.get("HF_HUB_OFFLINE") == "1"
            try:
                _EMBEDDING_MODELS[model_name] = SentenceTransformer(
                    model_name,
                    local_files_only=local_only,
                )
            except Exception:  # noqa: BLE001
                _EMBEDDING_MODELS[model_name] = SentenceTransformer(model_name)
        return _EMBEDDING_MODELS[model_name]


def compute_file_sha256(path: Path) -> str:
    """Computes SHA-256 hash of a file."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class VectorIndex:
    """Manages a FAISS IndexFlatIP index with DocChunks and IndexManifest."""

    def __init__(
        self,
        index_name: str,
        index_dir: Path,
        source_path: Path,
        settings: KnowledgeSettings | None = None,
        rebuild_builder: Callable[[], list[DocChunk]] | None = None,
    ) -> None:
        self.index_name = index_name
        self.index_dir = index_dir
        self.source_path = source_path
        self.settings = settings or get_settings()
        self.rebuild_builder = rebuild_builder

        self.index: faiss.Index | None = None
        self.chunks: list[DocChunk] = []
        self.manifest: IndexManifest | None = None
        self._lock = threading.Lock()

    @property
    def manifest_path(self) -> Path:
        return self.index_dir / "manifest.json"

    @property
    def index_path(self) -> Path:
        return self.index_dir / "index.faiss"

    @property
    def chunks_path(self) -> Path:
        return self.index_dir / "chunks.jsonl"

    def is_built(self) -> bool:
        """Returns True if all index artifacts exist on disk."""
        return (
            self.manifest_path.exists() and self.index_path.exists() and self.chunks_path.exists()
        )

    def is_stale(self) -> tuple[bool, str]:
        """Checks if the on-disk index is stale compared to settings and source file."""
        if not self.is_built():
            return True, "Index files are missing"

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                manifest = IndexManifest.model_validate_json(f.read())
        except (OSError, ValidationError, ValueError) as exc:
            return True, f"Failed to parse manifest: {exc}"

        if manifest.embedding_model != self.settings.embedding_model:
            return (
                True,
                f"Embedding model mismatch: index has '{manifest.embedding_model}', settings require '{self.settings.embedding_model}'",
            )

        if self.source_path.exists():
            current_hash = compute_file_sha256(self.source_path)
            if manifest.source_sha256 != current_hash:
                return (
                    True,
                    f"Source hash mismatch: index has '{manifest.source_sha256[:10]}...', current is '{current_hash[:10]}...'",
                )

        return False, ""

    def build(self, chunks: list[DocChunk], batch_size: int = 64) -> None:
        """Encodes chunks, creates FAISS index, and persists all artifacts."""
        with self._lock:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            model = get_embedding_model(self.settings.embedding_model)

            texts = [c.text for c in chunks]
            if not texts:
                raise ValueError("Cannot build index from empty chunk list")

            # Encode in batches with L2 normalization
            raw_embeddings = model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            embeddings = np.array(raw_embeddings, dtype=np.float32)
            faiss.normalize_L2(embeddings)

            dim = embeddings.shape[1]
            faiss_index = faiss.IndexFlatIP(dim)
            faiss_index.add(embeddings)

            # Persist FAISS index
            faiss.write_index(faiss_index, str(self.index_path))

            # Persist chunks.jsonl
            with open(self.chunks_path, "w", encoding="utf-8") as f:
                f.writelines(chunk.model_dump_json() + "\n" for chunk in chunks)

            # Create and persist manifest
            source_hash = compute_file_sha256(self.source_path) if self.source_path.exists() else ""
            manifest = IndexManifest(
                schema_version=1,
                index_name=self.index_name,
                embedding_model=self.settings.embedding_model,
                dim=dim,
                source_file=self.source_path.name,
                source_sha256=source_hash,
                chunk_count=len(chunks),
                built_at=datetime.now(timezone.utc).isoformat(),
            )
            with open(self.manifest_path, "w", encoding="utf-8") as f:
                f.write(manifest.model_dump_json(indent=2))

            # Update memory state
            self.index = faiss_index
            self.chunks = list(chunks)
            self.manifest = manifest

    def load(self, auto_rebuild: bool | None = None) -> None:
        """Loads index and chunks into memory, verifying freshness."""
        with self._lock:
            if self.index is not None and self.manifest is not None:
                return

            should_auto_rebuild = (
                auto_rebuild if auto_rebuild is not None else self.settings.auto_rebuild
            )

            stale, reason = self.is_stale()
            if stale:
                if should_auto_rebuild and self.rebuild_builder is not None:
                    chunks = self.rebuild_builder()
                    self.build(chunks)
                    return
                raise StaleIndexError(
                    f"Index '{self.index_name}' at {self.index_dir} is stale: {reason}"
                )

            # Load manifest
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                self.manifest = IndexManifest.model_validate_json(f.read())

            # Load FAISS index
            self.index = faiss.read_index(str(self.index_path))

            # Load chunks
            loaded_chunks: list[DocChunk] = []
            with open(self.chunks_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        loaded_chunks.append(DocChunk.model_validate_json(line))
            self.chunks = loaded_chunks

    def search(
        self, query: str, top_k: int = 5, min_score: float = 0.0
    ) -> list[tuple[DocChunk, float]]:
        """Searches the vector index and returns top matching chunks with scores."""
        if self.index is None:
            self.load()

        if self.index is None or not self.chunks:
            return []

        model = get_embedding_model(self.settings.embedding_model)
        query_emb = model.encode([query], normalize_embeddings=True)
        query_vec = np.array(query_emb, dtype=np.float32)
        faiss.normalize_L2(query_vec)

        k = min(top_k, len(self.chunks))
        scores, indices = self.index.search(query_vec, k)

        results: list[tuple[DocChunk, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx != -1 and float(score) >= min_score:
                results.append((self.chunks[idx], float(score)))

        return results
