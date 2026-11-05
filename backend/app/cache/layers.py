"""Multi-tier cache coordination (L1 Final, L2 Agent Outputs, L3 Similarity) and cacheability rules."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.agents.models import SAPAnalysis, SQLAnalysis
from app.cache.backends import CacheBackend
from app.cache.models import CacheEntry, ProducedBy
from app.cache.stats import get_stats_tracker
from app.synthesizer.models import BusinessIntelligence

logger = logging.getLogger(__name__)


def is_cacheable(
    bi: BusinessIntelligence,
) -> bool:
    """Evaluates whether an analysis result is eligible for caching.

    Rules:
      - degraded must be False
      - ai_generated must be True
      - no agent can be blocked
      - validation flags must be ok (opportunity_sql_ok, error_sql_ok, subset_ok)
      - no high or critical severity guardrail events.
    """
    if not bi.meta.ai_generated:
        return False
    if bi.meta.degraded:
        return False

    val = bi.meta.validation
    if not (val.opportunity_sql_ok and val.error_sql_ok and val.subset_ok):
        return False

    # Check guardrails report
    gr = bi.meta.guardrails
    return not (gr.blocked or gr.highest_severity in ("high", "critical"))


async def lookup_l1(
    key: str,
    backend: CacheBackend,
) -> BusinessIntelligence | None:
    """Performs L1 final cache lookup with cryptographic integrity verification."""
    raw = await backend.get(key)
    if not raw:
        get_stats_tracker().record_l1_miss()
        return None

    try:
        entry = CacheEntry[BusinessIntelligence].model_validate_json(raw)
        if not entry.verify_integrity():
            logger.warning(
                "Cache integrity verification failed for key '%s'; treating as miss", key
            )
            get_stats_tracker().record_l1_miss()
            return None

        # Increment hit count in storage
        entry.hit_count += 1
        bi = entry.payload

        # Update metadata for cache hit
        bi.meta.cache_status = "hit_final"
        bi.meta.cached_at = entry.created_at

        get_stats_tracker().record_l1_hit(
            tokens=bi.meta.token_usage.total,
            cost=bi.meta.estimated_cost_usd or 0.0,
        )
        return bi

    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed deserializing cached L1 entry for key '%s': %s", key, exc)
        get_stats_tracker().record_l1_miss()
        return None


async def write_l1(
    key: str,
    bi: BusinessIntelligence,
    backend: CacheBackend,
    ttl_seconds: int = 86400,
) -> bool:
    """Stores an eligible BusinessIntelligence result into L1 cache."""
    if not is_cacheable(bi):
        return False

    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + ttl_seconds, tz=timezone.utc
    ).isoformat()

    produced_by = ProducedBy(
        model=bi.meta.model,
        prompt_versions={
            "sql": bi.meta.prompt_versions.sql,
            "sap": bi.meta.prompt_versions.sap,
            "synthesizer": bi.meta.prompt_versions.synthesizer,
        },
        knowledge_fingerprint="",
    )

    entry = CacheEntry.create(
        key=key,
        kind="L1_final",
        payload=bi,
        created_at=now_iso,
        expires_at=expires_iso,
        produced_by=produced_by,
    )

    return await backend.set(key, entry.model_dump_json(), ttl_seconds=ttl_seconds)


async def lookup_l2_sql(
    key: str,
    backend: CacheBackend,
) -> SQLAnalysis | None:
    """Performs L2 SQL Agent cache lookup with integrity verification."""
    raw = await backend.get(key)
    if not raw:
        get_stats_tracker().record_l2_sql(hit=False)
        return None

    try:
        entry = CacheEntry[SQLAnalysis].model_validate_json(raw)
        if not entry.verify_integrity():
            logger.warning("Cache integrity verification failed for L2 SQL key '%s'", key)
            get_stats_tracker().record_l2_sql(hit=False)
            return None

        get_stats_tracker().record_l2_sql(hit=True)
        return entry.payload
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed deserializing cached L2 SQL entry: %s", exc)
        get_stats_tracker().record_l2_sql(hit=False)
        return None


async def write_l2_sql(
    key: str,
    analysis: SQLAnalysis,
    backend: CacheBackend,
    model: str,
    prompt_version: str,
    ttl_seconds: int = 86400,
) -> bool:
    """Stores a successful SQLAnalysis into L2 cache."""
    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + ttl_seconds, tz=timezone.utc
    ).isoformat()

    produced = ProducedBy(model=model, prompt_versions={"sql": prompt_version})
    entry = CacheEntry.create(
        key=key,
        kind="L2_sql",
        payload=analysis,
        created_at=now_iso,
        expires_at=expires_iso,
        produced_by=produced,
    )
    return await backend.set(key, entry.model_dump_json(), ttl_seconds=ttl_seconds)


async def lookup_l2_sap(
    key: str,
    backend: CacheBackend,
) -> SAPAnalysis | None:
    """Performs L2 SAP Domain Agent cache lookup with integrity verification."""
    raw = await backend.get(key)
    if not raw:
        get_stats_tracker().record_l2_sap(hit=False)
        return None

    try:
        entry = CacheEntry[SAPAnalysis].model_validate_json(raw)
        if not entry.verify_integrity():
            logger.warning("Cache integrity verification failed for L2 SAP key '%s'", key)
            get_stats_tracker().record_l2_sap(hit=False)
            return None

        get_stats_tracker().record_l2_sap(hit=True)
        return entry.payload
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed deserializing cached L2 SAP entry: %s", exc)
        get_stats_tracker().record_l2_sap(hit=False)
        return None


async def write_l2_sap(
    key: str,
    analysis: SAPAnalysis,
    backend: CacheBackend,
    model: str,
    prompt_version: str,
    knowledge_fingerprint: str = "",
    ttl_seconds: int = 86400,
) -> bool:
    """Stores a successful SAPAnalysis into L2 cache."""
    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() + ttl_seconds, tz=timezone.utc
    ).isoformat()

    produced = ProducedBy(
        model=model,
        prompt_versions={"sap": prompt_version},
        knowledge_fingerprint=knowledge_fingerprint,
    )
    entry = CacheEntry.create(
        key=key,
        kind="L2_sap",
        payload=analysis,
        created_at=now_iso,
        expires_at=expires_iso,
        produced_by=produced,
    )
    return await backend.set(key, entry.model_dump_json(), ttl_seconds=ttl_seconds)
