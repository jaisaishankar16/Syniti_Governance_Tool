"""Smoke test script for verifying AI gateway and LLM connectivity.

Uses only .env and client factory settings.
Outputs model, gateway host only, latency, response text, and token usage.
Exits non-zero with a classified error code on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend is on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.agents.client_factory import run_smoke_ping
from app.agents.settings import get_llm_settings


def main() -> None:
    settings = get_llm_settings()
    result = run_smoke_ping(settings)

    print("=" * 60)
    print("                LLM GATEWAY SMOKE TEST")
    print("=" * 60)
    print(f"Model:        {result.get('model', settings.llm_model)}")
    print(f"Gateway Host: {result.get('gateway_host', 'unknown')}")
    print(f"Latency:      {result.get('latency_ms', 0.0):.2f} ms")

    if result.get("success"):
        print(f"Response:     {result.get('response_text')}")
        usage = result.get("usage", {})
        print(
            f"Tokens:       prompt={usage.get('prompt_tokens', 0)}, "
            f"completion={usage.get('completion_tokens', 0)}, "
            f"total={usage.get('total_tokens', 0)}"
        )
        print("Status:       SUCCESS (OK)")
        print("=" * 60)
        sys.exit(0)
    else:
        err_code = result.get("error_code", "LLM_UNAVAILABLE")
        msg = result.get("message", "Unknown error")
        print(f"Status:       FAILED [{err_code}]")
        print(f"Details:      {msg}")
        print("=" * 60)
        # Exit non-zero with classified error code
        sys.exit(1)


if __name__ == "__main__":
    main()

