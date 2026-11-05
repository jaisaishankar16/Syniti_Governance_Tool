"""Exports the application OpenAPI schema to docs/openapi.json for client code generation."""

from __future__ import annotations

import json
from pathlib import Path

from app.api.main import create_app
from app.api.settings import ApiSettings


def export_openapi() -> None:
    settings = ApiSettings(app_env="development", docs_enabled=True)
    app = create_app(settings=settings, skip_index_check=True)

    openapi_schema = app.openapi()

    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    output_path = docs_dir / "openapi.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(openapi_schema, f, indent=2)

    print(f"SUCCESS: Exported OpenAPI specification to {output_path}")


if __name__ == "__main__":
    export_openapi()
