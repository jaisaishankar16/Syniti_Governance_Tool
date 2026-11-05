"""In-memory cache for recent auth and quota provider failures (60-second fail-fast window).

Under Phase 5.1 rules:
  - 401 (LLM_AUTH_FAILED) and 429 quota (LLM_QUOTA_EXCEEDED) failures are remembered for 60 seconds.
  - During this window, incoming requests fail fast with the identical typed error WITHOUT calling the provider.
  - A successful call or successful admin ping clears the cache.
  - These errors do not count toward circuit breaker failure counts.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from app.errors import AppError, ErrorCode


class LLMFailureCache:
    """Remembers 401 (auth) and 429 (quota) provider failures for 60 seconds."""

    def __init__(self, ttl_s: float = 60.0) -> None:
        self.ttl_s = ttl_s
        self._last_error: AppError | None = None
        self._last_error_code: str | None = None
        self._last_error_at: datetime | None = None
        self._expires_at: float = 0.0

    def record_failure(self, error: AppError) -> None:
        """Records an auth or quota error for 60 seconds."""
        if error.code in (ErrorCode.LLM_AUTH_FAILED, ErrorCode.LLM_QUOTA_EXCEEDED):
            now = time.monotonic()
            self._last_error = error
            self._last_error_code = error.code.value
            self._last_error_at = datetime.now(timezone.utc)
            self._expires_at = now + self.ttl_s

    def clear(self) -> None:
        """Clears any recorded failure window (e.g. after a successful ping or call)."""
        self._last_error = None
        self._last_error_code = None
        self._last_error_at = None
        self._expires_at = 0.0

    def check_failure_window(self) -> None:
        """Raises the cached AppError if currently within an active 60s failure window."""
        if self._last_error is not None:
            if time.monotonic() < self._expires_at:
                raise self._last_error
            # Window has passed
            self.clear()

    def get_status(self) -> tuple[str | None, str | None]:
        """Returns (last_error_code, last_error_at_iso) if active, else (None, None)."""
        if self._last_error is not None:
            if time.monotonic() < self._expires_at:
                iso = self._last_error_at.isoformat() if self._last_error_at else None
                return self._last_error_code, iso
            self.clear()
        return None, None


_GLOBAL_FAILURE_CACHE = LLMFailureCache(ttl_s=60.0)


def get_failure_cache() -> LLMFailureCache:
    """Returns singleton LLMFailureCache instance."""
    return _GLOBAL_FAILURE_CACHE
