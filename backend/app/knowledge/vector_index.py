"""Vector index implementation using FAISS IndexFlatIP with OpenAI-compatible embeddings."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import faiss  # type: ignore[import-untyped]
import numpy as np
from pydantic import ValidationError

from app.embeddings.cache import get_disk_cache
from app.embeddings.embedder import Embedder, get_embedder
from app.embeddings.settings import EmbeddingSettings, get_embedding_settings
from app.knowledge.models import DocChunk, IndexManifest, StaleIndexError
from app.knowledge.settings import KnowledgeSettings, get_settings


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
    """Manages a FAISS IndexFlatIP index with DocChunks and IndexManifest v2."""

    def __init__(
        self,
        index_name: str,
        index_dir: Path,
        source_path: Path,
        settings: KnowledgeSettings | None = None,
        embedding_settings: EmbeddingSettings | None = None,
        embedder: Embedder | None = None,
        rebuild_builder: Callable[[], list[DocChunk]] | None = None,
    ) -> None:
        self.index_name = index_name
        self.index_dir = index_dir
        self.source_path = source_path
        self.settings = settings or get_settings()
        self.embedding_settings = embedding_settings or get_embedding_settings()
        self.embedder = embedder or get_embedder(self.embedding_settings)
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
        """Checks if the on-disk index is stale compared to embedding settings and source file."""
        if not self.is_built():
            return True, "Index files are missing"

        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                manifest = IndexManifest.model_validate_json(f.read())
        except (OSError, ValidationError, ValueError) as exc:
            return True, f"Failed to parse manifest: {exc}"

        if manifest.schema_version != 2:
            return (
                True,
                f"Manifest schema version mismatch: found v{manifest.schema_version}, expected v2",
            )

        if manifest.embedding_model != self.embedding_settings.embedding_model:
            return (
                True,
                f"Embedding model mismatch: index has '{manifest.embedding_model}', settings require '{self.embedding_settings.embedding_model}'",
            )

        if (
            self.embedding_settings.embedding_dimensions is not None
            and manifest.dimensions != self.embedding_settings.embedding_dimensions
        ):
            return (
                True,
                f"Dimensions mismatch: index has {manifest.dimensions}, settings require {self.embedding_settings.embedding_dimensions}",
            )

        if manifest.base_url_hash != self.embedding_settings.base_url_hash:
            return (
                True,
                f"Base URL hash mismatch: index has '{manifest.base_url_hash}', active is '{self.embedding_settings.base_url_hash}'",
            )

        if self.source_path.exists():
            current_hash = compute_file_sha256(self.source_path)
            if manifest.source_sha256 != current_hash:
                return (
                    True,
                    f"Source hash mismatch: index has '{manifest.source_sha256[:10]}...', current is '{current_hash[:10]}...'",
                )

        return False, ""

    def build(self, chunks: list[DocChunk], batch_size: int = 96) -> None:
        """Encodes chunks, creates FAISS index, and persists all artifacts using disk cache."""
        with self._lock:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            if not chunks:
                raise ValueError("Cannot build index from empty chunk list")

            model_name = self.embedding_settings.embedding_model
            dims = self.embedding_settings.embedding_dimensions
            disk_cache = get_disk_cache()

            # Retrieve cached embeddings or record cache misses
            chunk_vectors: list[list[float] | None] = [None] * len(chunks)
            missing_indices: list[int] = []
            missing_texts: list[str] = []

            for i, chunk in enumerate(chunks):
                cached_vec = disk_cache.get(model_name, dims, chunk.text)
                if cached_vec is not None:
                    chunk_vectors[i] = cached_vec
                else:
                    missing_indices.append(i)
                    missing_texts.append(chunk.text)

            # Embed missing chunks
            if missing_texts:
                result = self.embedder.embed_documents_sync(missing_texts)
                for missing_pos, vec in enumerate(result.vectors):
                    orig_idx = missing_indices[missing_pos]
                    chunk_vectors[orig_idx] = vec
                    disk_cache.put(model_name, dims, missing_texts[missing_pos], vec)

            valid_vectors = [v for v in chunk_vectors if v is not None]
            if len(valid_vectors) != len(chunks):
                raise ValueError(f"Failed to generate embeddings for all chunks ({len(valid_vectors)}/{len(chunks)})")

            embeddings = np.array(valid_vectors, dtype=np.float32)
            faiss.normalize_L2(embeddings)

            dim = int(embeddings.shape[1])
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
                schema_version=2,
                provider="OpenAI-compatible",
                index_name=self.index_name,
                embedding_model=model_name,
                dimensions=dim,
                base_url_hash=self.embedding_settings.base_url_hash,
                normalized=True,
                source_file=self.source_path.name,
                source_sha256=source_hash,
                chunk_count=len(chunks),
                built_at=datetime.now(timezone.utc).isoformat(),
                chunk_id_scheme="deterministic_v1",
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
                    f"Index '{self.index_name}' at {self.index_dir} is stale: {reason}. "
                    "Rebuild with: python -m app.knowledge.build_index --target all"
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

        query_vec_raw = self.embedder.embed_query_sync(query)
        query_vec = np.array([query_vec_raw], dtype=np.float32)
        faiss.normalize_L2(query_vec)

        k = min(top_k, len(self.chunks))
        scores, indices = self.index.search(query_vec, k)

        results: list[tuple[DocChunk, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx != -1 and float(score) >= min_score:
                results.append((self.chunks[idx], float(score)))

        return results
