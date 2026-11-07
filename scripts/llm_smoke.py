"""Smoke test script for verifying AI gateway and LLM/embeddings connectivity.

Supports:
  python scripts/llm_smoke.py              (tests chat completion model)
  python scripts/llm_smoke.py --embeddings (tests embedding model)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure backend is on sys.path
repo_root = Path(__file__).resolve().parent.parent
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.agents.client_factory import run_smoke_ping
from app.agents.settings import get_llm_settings
from app.embeddings.embedder import OpenAIEmbedder
from app.embeddings.settings import get_embedding_settings


def smoke_embeddings() -> None:
    emb_settings = get_embedding_settings()
    if not emb_settings.is_configured:
        print("=" * 60)
        print("             EMBEDDINGS GATEWAY SMOKE TEST")
        print("=" * 60)
        print("Status:       FAILED [LLM_AUTH_FAILED]")
        print("Details:      No valid API key configured for embeddings.")
        print("=" * 60)
        sys.exit(1)

    print("=" * 60)
    print("             EMBEDDINGS GATEWAY SMOKE TEST")
    print("=" * 60)
    print(f"Model:        {emb_settings.embedding_model}")
    print(f"Gateway Host: {emb_settings.base_host}")

    t0 = time.perf_counter()
    embedder = OpenAIEmbedder(settings=emb_settings)
    try:
        res = embedder.embed_documents_sync(["smoke ping probe"])
        latency_ms = (time.perf_counter() - t0) * 1000
        dims = res.dimensions
        usage = res.usage

        print(f"Latency:      {latency_ms:.2f} ms")
        print(f"Dimensions:   {dims}")
        print(
            f"Tokens:       prompt={usage.prompt_tokens}, total={usage.total_tokens}"
        )
        print("Status:       SUCCESS (OK)")
        print("=" * 60)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - t0) * 1000
        err_code = getattr(exc, "code", "EMBEDDING_UNAVAILABLE")
        print(f"Latency:      {latency_ms:.2f} ms")
        print(f"Status:       FAILED [{err_code}]")
        print(f"Details:      {exc}")
        print("=" * 60)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test for LLM and Embeddings gateway connectivity.")
    parser.add_argument("--embeddings", action="store_true", help="Probe embeddings endpoint instead of chat")
    args = parser.parse_args()

    if args.embeddings:
        smoke_embeddings()
        return

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
        sys.exit(1)


if __name__ == "__main__":
    main()
