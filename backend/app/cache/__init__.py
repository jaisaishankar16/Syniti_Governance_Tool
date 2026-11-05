"""Phase 4 Multi-Tier Caching System (L1 Final, L2 Agent Outputs, L3 Similarity)."""

from app.cache.backends import (
    CacheBackend,
    InMemoryBackend,
    RedisBackend,
    get_cache_backend,
)
from app.cache.keys import (
    CACHE_SCHEMA,
    compute_knowledge_fingerprint,
    make_cache_key,
    make_l1_key,
    make_l2_sap_key,
    make_l2_sql_key,
)
from app.cache.layers import (
    is_cacheable,
    lookup_l1,
    lookup_l2_sap,
    lookup_l2_sql,
    write_l1,
    write_l2_sap,
    write_l2_sql,
)
from app.cache.models import CacheEntry, ProducedBy
from app.cache.similarity import (
    SimilarityCacheIndex,
    compute_structure_fingerprint,
    get_similarity_index,
    mask_literals_and_aliases,
)
from app.cache.singleflight import SingleFlight, get_single_flight
from app.cache.stats import CacheStats, CacheStatsTracker, get_stats_tracker

__all__ = [
    "CACHE_SCHEMA",
    "CacheBackend",
    "CacheEntry",
    "CacheStats",
    "CacheStatsTracker",
    "InMemoryBackend",
    "ProducedBy",
    "RedisBackend",
    "SimilarityCacheIndex",
    "SingleFlight",
    "compute_knowledge_fingerprint",
    "compute_structure_fingerprint",
    "get_cache_backend",
    "get_similarity_index",
    "get_single_flight",
    "get_stats_tracker",
    "is_cacheable",
    "lookup_l1",
    "lookup_l2_sap",
    "lookup_l2_sql",
    "make_cache_key",
    "make_l1_key",
    "make_l2_sap_key",
    "make_l2_sql_key",
    "mask_literals_and_aliases",
    "write_l1",
    "write_l2_sap",
    "write_l2_sql",
]
