"""Progress reporting models and callbacks for streaming pipeline events."""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

StageName = Literal[
    "guardrails",
    "parsing",
    "cache_lookup",
    "agents_running",
    "synthesizing",
    "validating",
    "done",
    "error",
]


class ProgressEvent(BaseModel):
    """Event emitted at key transition points in the analysis pipeline."""

    model_config = ConfigDict(extra="forbid")

    stage: StageName
    message: str
    elapsed_ms: float
    detail: dict[str, Any] = Field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], Awaitable[None] | None]


async def emit_progress(
    callback: ProgressCallback | None,
    stage: StageName,
    message: str,
    start_time: float,
    detail: dict[str, Any] | None = None,
) -> None:
    """Dispatches a progress event to the callback if registered."""
    if callback is None:
        return

    elapsed = round((time.perf_counter() - start_time) * 1000, 2)
    ev = ProgressEvent(
        stage=stage,
        message=message,
        elapsed_ms=elapsed,
        detail=detail or {},
    )

    try:
        res = callback(ev)
        if inspect.isawaitable(res):
            await res
    except Exception:  # noqa: S110, BLE001
        # Progress callback failures must never crash the pipeline
        pass
