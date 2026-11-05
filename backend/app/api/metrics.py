"""Prometheus metrics instrumentation for HTTP, LLM, cache, and guardrails."""

from __future__ import annotations

import logging

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

# Dedicated registry to prevent collision with other libraries
REGISTRY = CollectorRegistry(auto_describe=True)

# 1. HTTP Metrics
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total number of HTTP requests processed",
    ["route", "method", "status"],
    registry=REGISTRY,
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["route"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
    registry=REGISTRY,
)

# 2. Analyze Pipeline Metrics
ANALYZE_TOTAL = Counter(
    "analyze_total",
    "Total SQL analysis requests executed",
    ["cache_status", "degraded", "outcome"],
    registry=REGISTRY,
)

ANALYZE_STAGE_DURATION_SECONDS = Histogram(
    "analyze_stage_duration_seconds",
    "Duration of individual analysis pipeline stages in seconds",
    ["stage"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    registry=REGISTRY,
)

# 3. LLM Tokens & Errors
LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Total tokens consumed by LLM agents",
    ["agent", "kind"],
    registry=REGISTRY,
)

LLM_REQUEST_ERRORS_TOTAL = Counter(
    "llm_request_errors_total",
    "Total LLM invocation errors grouped by error code",
    ["code"],
    registry=REGISTRY,
)

# 4. Guardrails & Safety
GUARDRAIL_EVENTS_TOTAL = Counter(
    "guardrail_events_total",
    "Total security and validation guardrail events",
    ["name", "action", "severity"],
    registry=REGISTRY,
)

# 5. Caching Metrics
CACHE_REQUESTS_TOTAL = Counter(
    "cache_requests_total",
    "Multi-tier cache lookup outcomes",
    ["layer", "result"],
    registry=REGISTRY,
)

# 6. Operational Gauges & Rate Limiting
CIRCUIT_BREAKER_STATE = Gauge(
    "circuit_breaker_state",
    "Current state of the LLM circuit breaker (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
    registry=REGISTRY,
)

RATE_LIMITED_TOTAL = Counter(
    "rate_limited_total",
    "Total requests throttled by rate limiters",
    ["bucket"],
    registry=REGISTRY,
)

SSE_ACTIVE_STREAMS = Gauge(
    "sse_active_streams",
    "Current active Server-Sent Events (SSE) connections",
    registry=REGISTRY,
)


def get_metrics_text() -> bytes:
    """Generates Prometheus format metric report."""
    return generate_latest(REGISTRY)
