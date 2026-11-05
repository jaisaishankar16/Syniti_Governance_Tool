"""Programmatic launcher for the FastAPI application server."""

from __future__ import annotations

import asyncio
import sys

import uvicorn
from app.api.settings import get_api_settings


def main() -> None:
    """Entry point starting uvicorn with Windows-compatible event loop policy."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    settings = get_api_settings()
    is_dev = settings.app_env.lower() == "development"

    print("=" * 80)
    print("           MDM SYNITI ANALYSIS ORCHESTRATOR API SERVER")
    print("=" * 80)
    print(f"Environment:   {settings.app_env.upper()}")
    print(f"Binding:       http://{settings.host}:{settings.port}")
    print(f"Docs enabled:  {settings.docs_enabled} (Swagger: /docs)")
    print(f"Metrics:       {'Active' if settings.metrics_enabled else 'Disabled'} (/metrics)")
    print(f"Hot Reload:    {'Enabled' if is_dev else 'Disabled'}")
    print("=" * 80)

    uvicorn.run(
        "app.api.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=is_dev,
        log_level=settings.log_level.lower(),
        timeout_keep_alive=int(settings.sse_heartbeat_s + 15),
    )


if __name__ == "__main__":
    main()
