"""Unit tests for token budget tracking, rate limiter, concurrency, and circuit breaker."""

import asyncio
import time

import pytest
from app.guardrails.budget import (
    BudgetTracker,
    ConcurrencyManager,
    InMemoryRateLimiter,
    calculate_cost_usd,
)
from app.guardrails.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    is_transient_error,
)
from app.guardrails.settings import GuardrailSettings


def test_calculate_cost_usd() -> None:
    pricing = {"gpt-4o-mini": {"input_per_m": 0.15, "output_per_m": 0.60}}
    cost = calculate_cost_usd("gpt-4o-mini", 10_000, 2_000, pricing)
    # (10000 / 1e6)*0.15 = 0.0015; (2000 / 1e6)*0.60 = 0.0012; total = 0.0027
    assert cost == 0.0027

    # Empty pricing table / unconfigured model returns None ("pricing not configured")
    assert calculate_cost_usd("gpt-4o-mini", 10_000, 2_000, {}) is None
    assert calculate_cost_usd("unconfigured-model", 10_000, 2_000, pricing) is None


def test_budget_tracker_limits() -> None:
    settings = GuardrailSettings(
        per_request_token_budget=5_000,
        daily_token_budget=10_000,
    )
    tracker = BudgetTracker()

    # Request within budget
    v1 = tracker.check_request_budget(2_000, settings)
    assert v1.allowed is True

    # Request exceeding budget
    v2 = tracker.check_request_budget(6_000, settings)
    assert v2.allowed is False
    assert any(e.name == "request_token_budget_exceeded" for e in v2.report.events)

    # Record usage within daily budget
    usage, rep1 = tracker.record_usage(2_000, 1_000, "gpt-4o-mini", settings)
    assert usage.total_tokens == 3_000
    assert not rep1.blocked

    # Record usage exceeding daily budget
    _, rep2 = tracker.record_usage(5_000, 3_000, "gpt-4o-mini", settings)
    assert rep2.blocked is True
    assert any(e.name == "daily_token_budget_exceeded" for e in rep2.events)


def test_concurrency_manager() -> None:
    cm = ConcurrencyManager(max_concurrent=2)
    active = 0
    max_active = 0

    async def worker() -> None:
        nonlocal active, max_active
        async with cm:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1

    async def run_all() -> None:
        await asyncio.gather(*(worker() for _ in range(5)))

    asyncio.run(run_all())
    assert max_active <= 2


def test_in_memory_rate_limiter() -> None:
    limiter = InMemoryRateLimiter(max_requests=3, window_seconds=1.0)
    assert limiter.allow("client_1") is True
    assert limiter.allow("client_1") is True
    assert limiter.allow("client_1") is True
    assert limiter.allow("client_1") is False  # Exceeded limit
    assert limiter.allow("client_2") is True  # Separate client allowed


def test_circuit_breaker_transient_classification() -> None:
    assert is_transient_error(TimeoutError("Timeout")) is True
    assert is_transient_error(ConnectionError("Connection lost")) is True
    assert is_transient_error(RuntimeError("503 Service Unavailable")) is True
    assert is_transient_error(RuntimeError("Rate limit exceeded 429")) is True

    assert is_transient_error(ValueError("Invalid argument")) is False
    assert is_transient_error(RuntimeError("400 Bad Request")) is False


def test_circuit_breaker_state_machine() -> None:
    cb = CircuitBreaker(name="test_circuit", failure_threshold=2, reset_timeout_s=0.1)
    assert cb.state == CircuitState.CLOSED

    # Non-transient failure does not trip
    cb.record_failure(ValueError("400 Bad Request"))
    assert cb.failure_count == 0
    assert cb.state == CircuitState.CLOSED

    # First transient failure
    cb.record_failure(TimeoutError("Timeout 1"))
    assert cb.failure_count == 1
    assert cb.state == CircuitState.CLOSED

    # Second transient failure trips to OPEN
    cb.record_failure(TimeoutError("Timeout 2"))
    assert cb.failure_count == 2
    assert cb.state == CircuitState.OPEN

    # While OPEN, check_call_allowed raises CircuitBreakerOpenError
    with pytest.raises(CircuitBreakerOpenError):
        cb.check_call_allowed()

    # Wait for reset timeout
    time.sleep(0.12)
    assert cb.state == CircuitState.HALF_OPEN

    # Success in HALF_OPEN resets to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0
