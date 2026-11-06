"""Unit tests verifying env/.env.example stays synchronized with settings classes."""

from __future__ import annotations

from pathlib import Path


def test_env_example_contains_all_core_settings():
    """Ensures env/.env.example covers all required settings from LLMSettings and ApiSettings."""
    repo_root = Path(__file__).resolve().parents[2]
    example_path = repo_root / "env" / ".env.example"
    assert example_path.exists(), "env/.env.example must exist"

    text = example_path.read_text(encoding="utf-8")
    lines = [
        line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")
    ]
    keys = {line.split("=")[0].strip() for line in lines}

    required_llm_keys = {
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "LLM_MODEL",
        "LLM_API_MODE",
        "STRUCTURED_OUTPUT_MODE",
        "TOOLS_MODE",
        "LLM_CA_BUNDLE",
        "LLM_EXTRA_HEADERS",
        "LLM_TEMPERATURE",
        "LLM_MAX_TURNS",
        "LLM_TIMEOUT_S",
    }
    required_api_keys = {
        "APP_ENV",
        "HOST",
        "PORT",
        "RATE_LIMIT_BACKEND",
        "CACHE_BACKEND",
        "DAILY_TOKEN_BUDGET",
        "ACTIVITY_DATA_DIR",
        "PLACEHOLDER_PRINCIPAL_NAME",
        "PLACEHOLDER_PRINCIPAL_EMAIL",
        "ADMIN_API_KEY",
    }

    missing_llm = required_llm_keys - keys
    assert not missing_llm, f"env/.env.example missing required LLM settings: {missing_llm}"

    missing_api = required_api_keys - keys
    assert not missing_api, f"env/.env.example missing required API settings: {missing_api}"


def test_env_example_has_no_secrets():
    """Ensures env/.env.example contains only placeholders, never actual keys or tokens."""
    repo_root = Path(__file__).resolve().parents[2]
    example_path = repo_root / "env" / ".env.example"
    text = example_path.read_text(encoding="utf-8")

    forbidden_patterns = ["sk-proj-", "Bearer ", "ghp_", "AKIA"]
    for pattern in forbidden_patterns:
        assert pattern not in text, (
            f"Found forbidden secret pattern '{pattern}' in env/.env.example"
        )
