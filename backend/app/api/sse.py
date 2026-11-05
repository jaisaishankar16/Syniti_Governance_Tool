"""Server-Sent Events (SSE) streaming protocol implementation for analysis progress."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from app.api.metrics import SSE_ACTIVE_STREAMS
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class SseEvent(BaseModel):
    """Structured Server-Sent Event formatted according to W3C specification."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["progress", "result", "error", "done"] = Field(
        description="Event name classification"
    )
    data: str = Field(description="Serialized JSON string event payload")
    id: str | None = Field(default=None, description="Optional unique message event ID")
    retry: int | None = Field(default=None, description="Reconnection time in milliseconds")

    def encode(self) -> str:
        """Formats the event into standard SSE text block."""
        lines: list[str] = []
        if self.id is not None:
            lines.append(f"id: {self.id}")
        lines.append(f"event: {self.event}")
        for data_line in self.data.split("\n"):
            lines.append(f"data: {data_line}")
        lines.append("\n")
        return "\n".join(lines)


async def sse_event_generator(
    analysis_coro: asyncio.Future[Any] | asyncio.Task[Any],
    queue: asyncio.Queue[SseEvent | str | None],
    heartbeat_interval_s: float = 15.0,
    request_id: str = "",
) -> AsyncIterator[str]:
    """Streams SSE events and heartbeats to client while shielding underlying execution.

    Client disconnection stops writing output without cancelling the shielded task,
    ensuring single-flight synchronization and cache population complete normally.
    """
    SSE_ACTIVE_STREAMS.inc()
    try:
        while True:
            try:
                # Wait for next event or heartbeat timeout
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval_s)
                if item is None:
                    # Stream complete
                    break

                if isinstance(item, SseEvent):
                    yield item.encode()
                elif isinstance(item, str):
                    # Raw comment or string (e.g. heartbeat)
                    yield item

            except asyncio.TimeoutError:
                # Emit SSE heartbeat comment line
                yield ": heartbeat\n\n"

    except (asyncio.CancelledError, ConnectionResetError):
        logger.info(
            "Client disconnected from SSE stream for request %s; underlying task continues shielded in background",
            request_id,
        )
        raise

    finally:
        SSE_ACTIVE_STREAMS.dec()
