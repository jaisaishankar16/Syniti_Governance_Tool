"""Circuit breaker for resilient LLM calls preventing cascaded failures."""

from __future__ import annotations

import asyncio
import threading
import time
from enum import Enum
from typing import TypeVar

from app.guardrails.models import GuardrailBlocked, GuardrailEvent, GuardrailReport, KeyValue
from app.guardrails.settings import get_guardrail_settings

T = TypeVar("T")


class CircuitState(str, Enum):
    """Possible states for the circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(GuardrailBlocked):
    """Raised when an operation is attempted while the circuit is OPEN."""

    def __init__(self, message: str, report: GuardrailReport) -> None:
        super().__init__(message, report)


def is_transient_error(exc: BaseException) -> bool:
    """Determines if an exception represents a transient failure (network/timeout/rate-limit/5xx)."""
    # Exclude business validation & auth errors
    msg = str(exc).lower()
    exc_type = type(exc).__name__.lower()

    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True

    # Check for HTTP status codes commonly in error messages
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return True
    if any(
        code in msg for code in ("500", "502", "503", "504", "server error", "service unavailable")
    ):
        return True
    return bool("timeout" in exc_type or "connect" in exc_type)


class CircuitBreaker:
    """Thread-safe circuit breaker tracking consecutive transient failures."""

    def __init__(
        self,
        name: str = "llm_circuit",
        failure_threshold: int | None = None,
        reset_timeout_s: float | None = None,
    ) -> None:
        cfg = get_guardrail_settings()
        self.name = name
        self.failure_threshold = (
            failure_threshold if failure_threshold is not None else cfg.circuit_failure_threshold
        )
        self.reset_timeout_s = (
            reset_timeout_s if reset_timeout_s is not None else cfg.circuit_reset_s
        )

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._evaluate_state()
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def _evaluate_state(self) -> None:
        """Transitions OPEN -> HALF_OPEN if reset timeout has elapsed."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.reset_timeout_s:
                self._state = CircuitState.HALF_OPEN

    def record_success(self) -> None:
        """Records a successful operation; closes the circuit."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    def record_failure(self, exc: BaseException, request_id: str = "") -> GuardrailReport:
        """Records a failure; trips circuit to OPEN if transient threshold is met."""
        report = GuardrailReport()
        if not is_transient_error(exc):
            # Non-transient errors do not count toward circuit trips
            return report

        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                report.add_event(
                    GuardrailEvent(
                        name="circuit_breaker_tripped",
                        stage="budget",
                        action="block",
                        severity="critical",
                        message=f"Circuit '{self.name}' tripped OPEN after {self._failure_count} consecutive transient failures ({exc})",
                        details=[
                            KeyValue(key="failure_count", value=str(self._failure_count)),
                            KeyValue(key="threshold", value=str(self.failure_threshold)),
                            KeyValue(key="error", value=str(exc)),
                        ],
                        request_id=request_id,
                    )
                )

        return report

    def check_call_allowed(self, request_id: str = "") -> GuardrailReport:
        """Checks if a call is allowed; raises CircuitBreakerOpenError if OPEN."""
        with self._lock:
            self._evaluate_state()
            if self._state == CircuitState.OPEN:
                report = GuardrailReport()
                report.add_event(
                    GuardrailEvent(
                        name="circuit_breaker_open",
                        stage="budget",
                        action="block",
                        severity="critical",
                        message=f"Circuit '{self.name}' is OPEN; fast-failing call to prevent cascading failures",
                        details=[
                            KeyValue(key="failure_count", value=str(self._failure_count)),
                            KeyValue(key="reset_timeout_s", value=str(self.reset_timeout_s)),
                        ],
                        request_id=request_id,
                    )
                )
                raise CircuitBreakerOpenError(f"Circuit breaker '{self.name}' is OPEN", report)
        return GuardrailReport()

    def reset_for_test(self) -> None:
        """Resets the circuit breaker state (for testing)."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = 0.0


_GLOBAL_CIRCUIT_BREAKER = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    """Returns the singleton CircuitBreaker instance."""
    return _GLOBAL_CIRCUIT_BREAKER
