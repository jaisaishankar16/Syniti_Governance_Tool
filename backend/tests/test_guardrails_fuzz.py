"""Property-based fuzz testing of the input guardrails stage using Hypothesis."""

import time

from app.guardrails.models import GuardrailBlocked
from app.guardrails.pipeline import pre_flight_pipeline
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=2000))
def test_pre_flight_fuzzing_arbitrary_text(arbitrary_input: str) -> None:
    """Fuzzes pre_flight_pipeline with arbitrary text to guarantee resilience and bounded execution time."""
    start_time = time.perf_counter()

    try:
        sanitized = pre_flight_pipeline(arbitrary_input)
        # If it passed without blocking, it must be recognized as valid SQL structure
        assert isinstance(sanitized.sanitized_sql, str)
        assert isinstance(sanitized.canary_token, str)
    except GuardrailBlocked as exc:
        # Expected behavior for non-SQL or malicious inputs
        assert exc.report.blocked is True
        assert len(exc.report.events) > 0

    elapsed = time.perf_counter() - start_time
    # Must finish within 50 ms per input
    assert elapsed < 0.05, f"Fuzz iteration took too long: {elapsed:.4f}s"
