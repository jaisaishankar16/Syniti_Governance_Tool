"""Cache key generators, knowledge base fingerprinting, and namespace versioning."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.knowledge.store import KnowledgeStore

CACHE_SCHEMA = 1


def find_repo_root() -> Path:
    """Finds repository root anchoring on backend/app/cache/keys.py."""
    return Path(__file__).resolve().parents[3]


REPO_ROOT = find_repo_root()
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"


def compute_knowledge_fingerprint(
    store: KnowledgeStore | None = None,
    embedding_settings: Any | None = None,
) -> str:
    """Computes a stable sha256 fingerprint of the core knowledge base and active embedding configuration.

    Includes:
      - /knowledge/sap_tables_prebuilt.json
      - /knowledge/sap_common_fields.json
      - /knowledge/governance_glossary.json
      - /knowledge/sql_keywords.json
      - /knowledge/naming_conventions.json
      - All index manifests under /knowledge/index/
      - Active embedding model, dimensions, and provider base-URL hash.

    Explicitly excludes:
      - /knowledge/learned/* (learned entries) to avoid cache churn on every incremental write-back.
    """
    hasher = hashlib.sha256()

    files_to_hash = [
        KNOWLEDGE_DIR / "sap_tables_prebuilt.json",
        KNOWLEDGE_DIR / "sap_common_fields.json",
        KNOWLEDGE_DIR / "governance_glossary.json",
        KNOWLEDGE_DIR / "sql_keywords.json",
        KNOWLEDGE_DIR / "naming_conventions.json",
    ]

    # Index manifests (recursive to catch sap_docs/manifest.json, sql_docs/manifest.json)
    index_dir = KNOWLEDGE_DIR / "index"
    if index_dir.exists():
        files_to_hash.extend(sorted(index_dir.rglob("*manifest*.json")))

    for p in files_to_hash:
        if p.is_file():
            try:
                hasher.update(p.name.encode("utf-8"))
                hasher.update(p.read_bytes())
            except OSError:
                continue

    # Include embedding configuration
    try:
        if embedding_settings is None:
            from app.embeddings.settings import get_embedding_settings

            emb_s = get_embedding_settings()
        else:
            emb_s = embedding_settings

        emb_tag = f"{emb_s.embedding_model}:{emb_s.embedding_dimensions}:{emb_s.base_url_hash}"
        hasher.update(emb_tag.encode("utf-8"))
    except Exception:  # noqa: BLE001, S110
        pass

    return hasher.hexdigest()[:16]


def hash_base_url(base_url: str | None = None) -> str:
    """Computes a stable hash tag for the provider base URL to isolate caches between models/endpoints."""
    if not base_url:
        return "default"
    clean = base_url.strip().rstrip("/")
    if not clean:
        return "default"
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:8]


def make_cache_key(kind: str, digest: str) -> str:
    """Formats a standardized, versioned cache key.

    Format: syniti:v{CACHE_SCHEMA}:{kind}:{digest}
    """
    return f"syniti:v{CACHE_SCHEMA}:{kind}:{digest}"


def make_l1_key(
    normalized_sql: str,
    view_name: str | None,
    model: str,
    sql_prompt_version: str,
    sap_prompt_version: str,
    synth_prompt_version: str,
    knowledge_fingerprint: str,
    guardrail_policy_version: str = "v1.0",
    base_url: str | None = None,
) -> str:
    """Constructs L1 final cache key.

    Includes model and base_url hash so switching models or providers never serves cached answers.
    """
    url_tag = hash_base_url(base_url)
    payload = (
        f"{normalized_sql.strip()}|"
        f"{view_name or ''}|"
        f"{model.strip()}|"
        f"{url_tag}|"
        f"{sql_prompt_version}|"
        f"{sap_prompt_version}|"
        f"{synth_prompt_version}|"
        f"{knowledge_fingerprint}|"
        f"{guardrail_policy_version}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return make_cache_key("L1_final", digest)


def make_l2_sql_key(
    normalized_sql: str,
    sql_prompt_version: str,
    model: str,
    base_url: str | None = None,
) -> str:
    """Constructs L2 SQL Agent cache key."""
    url_tag = hash_base_url(base_url)
    payload = f"{normalized_sql.strip()}|{sql_prompt_version}|{model.strip()}|{url_tag}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return make_cache_key("L2_sql", digest)


def make_l2_sap_key(
    tables: list[str],
    fields: list[str],
    naming_facts: list[str],
    sap_prompt_version: str,
    model: str,
    knowledge_fingerprint: str,
    base_url: str | None = None,
) -> str:
    """Constructs L2 SAP Agent cache key from canonical sorted table and field identifiers."""
    url_tag = hash_base_url(base_url)
    sorted_tables = sorted(t.strip().upper() for t in tables)
    sorted_fields = sorted(f.strip().upper() for f in fields)
    sorted_naming = sorted(naming_facts)

    payload = (
        f"{','.join(sorted_tables)}|"
        f"{','.join(sorted_fields)}|"
        f"{','.join(sorted_naming)}|"
        f"{sap_prompt_version}|"
        f"{model.strip()}|"
        f"{url_tag}|"
        f"{knowledge_fingerprint}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return make_cache_key("L2_sap", digest)
