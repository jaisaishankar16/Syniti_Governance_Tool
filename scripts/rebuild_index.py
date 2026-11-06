"""Script to rebuild FAISS vector indexes and chunk manifests from knowledge sources."""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend directory to sys.path
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "backend"))

from app.knowledge.build_index import build_sap_index, build_sql_index
from app.knowledge.settings import get_settings


def main() -> None:
    print("Rebuilding knowledge indexes...")
    settings = get_settings()

    try:
        build_sap_index(settings=settings, rebuild=True)
    except Exception as e:
        print(f"Warning: Could not rebuild SAP index: {e}")

    try:
        build_sql_index(settings=settings, rebuild=True)
    except Exception as e:
        print(f"Warning: Could not rebuild SQL index: {e}")

    print("Knowledge index rebuild completed.")


if __name__ == "__main__":
    main()

