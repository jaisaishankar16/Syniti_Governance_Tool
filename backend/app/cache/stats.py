"""Cache metrics, hit/miss tracking, and administrative statistics."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.cache.backends import CacheBackend


class CacheStats(BaseModel):
    """Aggregate statistics and health metrics for the multi-tier caching system."""

    model_config = ConfigDict(extra="forbid")

    total_requests: int = 0
    l1_hits: int = 0
    l1_misses: int = 0
    l1_hit_ratio: float = 0.0
    l2_sql_hits: int = 0
    l2_sql_misses: int = 0
    l2_sap_hits: int = 0
    l2_sap_misses: int = 0
    l3_similarity_reuses: int = 0
    l3_similarity_hints: int = 0
    entries_count: int = 0
    backend_type: str = "memory"
    backend_healthy: bool = True
    estimated_tokens_saved: int = 0
    estimated_cost_saved_usd: float = 0.0


class CacheStatsTracker:
    """Thread-safe tracker for multi-tier cache hits, misses, and cost savings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_requests: int = 0
        self.l1_hits: int = 0
        self.l1_misses: int = 0
        self.l2_sql_hits: int = 0
        self.l2_sql_misses: int = 0
        self.l2_sap_hits: int = 0
        self.l2_sap_misses: int = 0
        self.l3_similarity_reuses: int = 0
        self.l3_similarity_hints: int = 0
        self.estimated_tokens_saved: int = 0
        self.estimated_cost_saved_usd: float = 0.0

    def record_l1_hit(self, tokens: int = 0, cost: float = 0.0) -> None:
        with self._lock:
            self.total_requests += 1
            self.l1_hits += 1
            self.estimated_tokens_saved += tokens
            self.estimated_cost_saved_usd += cost

    def record_l1_miss(self) -> None:
        with self._lock:
            self.total_requests += 1
            self.l1_misses += 1

    def record_l2_sql(self, hit: bool, tokens: int = 0, cost: float = 0.0) -> None:
        with self._lock:
            if hit:
                self.l2_sql_hits += 1
                self.estimated_tokens_saved += tokens
                self.estimated_cost_saved_usd += cost
            else:
                self.l2_sql_misses += 1

    def record_l2_sap(self, hit: bool, tokens: int = 0, cost: float = 0.0) -> None:
        with self._lock:
            if hit:
                self.l2_sap_hits += 1
                self.estimated_tokens_saved += tokens
                self.estimated_cost_saved_usd += cost
            else:
                self.l2_sap_misses += 1

    def record_l3_reuse(self, tokens: int = 0, cost: float = 0.0) -> None:
        with self._lock:
            self.l3_similarity_reuses += 1
            self.estimated_tokens_saved += tokens
            self.estimated_cost_saved_usd += cost

    def record_l3_hint(self) -> None:
        with self._lock:
            self.l3_similarity_hints += 1

    async def get_stats(self, backend: CacheBackend) -> CacheStats:
        """Returns structured cache stats combined with backend health and entry counts."""
        healthy = await backend.ping()
        keys = await backend.scan_prefix("syniti:")
        backend_name = type(backend).__name__.replace("Backend", "").lower()

        with self._lock:
            total_l1 = self.l1_hits + self.l1_misses
            ratio = round(self.l1_hits / total_l1, 4) if total_l1 > 0 else 0.0

            return CacheStats(
                total_requests=self.total_requests,
                l1_hits=self.l1_hits,
                l1_misses=self.l1_misses,
                l1_hit_ratio=ratio,
                l2_sql_hits=self.l2_sql_hits,
                l2_sql_misses=self.l2_sql_misses,
                l2_sap_hits=self.l2_sap_hits,
                l2_sap_misses=self.l2_sap_misses,
                l3_similarity_reuses=self.l3_similarity_reuses,
                l3_similarity_hints=self.l3_similarity_hints,
                entries_count=len(keys),
                backend_type=backend_name,
                backend_healthy=healthy,
                estimated_tokens_saved=self.estimated_tokens_saved,
                estimated_cost_saved_usd=round(self.estimated_cost_saved_usd, 6),
            )

    def reset_for_test(self) -> None:
        """Resets all metrics (for testing)."""
        with self._lock:
            self.total_requests = 0
            self.l1_hits = 0
            self.l1_misses = 0
            self.l2_sql_hits = 0
            self.l2_sql_misses = 0
            self.l2_sap_hits = 0
            self.l2_sap_misses = 0
            self.l3_similarity_reuses = 0
            self.l3_similarity_hints = 0
            self.estimated_tokens_saved = 0
            self.estimated_cost_saved_usd = 0.0


_GLOBAL_STATS_TRACKER = CacheStatsTracker()


def get_stats_tracker() -> CacheStatsTracker:
    """Returns singleton cache statistics tracker."""
    return _GLOBAL_STATS_TRACKER
