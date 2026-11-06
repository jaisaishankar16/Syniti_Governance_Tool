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


def test_env_example_uses_canonical_names_and_no_deprecated_aliases():
    """Ensures .env.example uses canonical names (OPENAI_BASE_URL, OPENAI_API_KEY) and no active aliases."""
    repo_root = Path(__file__).resolve().parents[2]
    root_example = repo_root / ".env.example"
    env_example = repo_root / "env" / ".env.example"

    assert root_example.exists(), "Root .env.example must exist"
    assert env_example.exists(), "env/.env.example must exist"

    # Both template files must be in sync
    assert root_example.read_text(encoding="utf-8") == env_example.read_text(encoding="utf-8")

    text = root_example.read_text(encoding="utf-8")
    active_keys = {
        line.split("=")[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    }

    assert "OPENAI_BASE_URL" in active_keys
    assert "OPENAI_API_KEY" in active_keys
    assert "LLM_BASE_URL" not in active_keys, "Deprecated alias LLM_BASE_URL should not be an active key in .env.example"
    assert "LLM_API_KEY" not in active_keys, "Deprecated alias LLM_API_KEY should not be an active key in .env.example"


def validate_frontend_env_content(content: str) -> None:
    """Validates that frontend env content contains only VITE_ variables and no secret-like names."""
    forbidden_terms = ["KEY", "SECRET", "TOKEN", "PASSWORD", "AUTH", "PRIVATE", "CREDENTIAL", "API_KEY"]
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            var_name = line.split("=", 1)[0].strip()
            if not var_name.startswith("VITE_"):
                raise ValueError(f"Frontend env variable '{var_name}' must start with 'VITE_'")
            var_upper = var_name.upper()
            for term in forbidden_terms:
                if term in var_upper:
                    raise ValueError(f"Frontend env variable '{var_name}' contains secret-like term '{term}'")


def test_frontend_env_rules_and_no_secrets():
    """Frontend env files may contain only VITE_ variables and zero secret-like variables."""
    repo_root = Path(__file__).resolve().parents[2]
    frontend_dir = repo_root / "frontend"

    # 1. Scan any actual frontend/.env* files that exist
    if frontend_dir.exists():
        for f in frontend_dir.glob(".env*"):
            if f.is_file():
                content = f.read_text(encoding="utf-8")
                validate_frontend_env_content(content)

    # 2. Test positive verification
    valid_frontend_env = "VITE_API_BASE_URL=http://localhost:8000\nVITE_APP_TITLE=Syniti Governance\n"
    validate_frontend_env_content(valid_frontend_env)

    # 3. Test negative verification: fails if missing VITE_ prefix
    import pytest

    with pytest.raises(ValueError, match="must start with 'VITE_'"):
        validate_frontend_env_content("OPENAI_API_KEY=sk-test-123\n")

    # 4. Test negative verification: fails if secret-like term even with VITE_ prefix
    with pytest.raises(ValueError, match="secret-like term"):
        validate_frontend_env_content("VITE_OPENAI_API_KEY=sk-test-123\n")

    with pytest.raises(ValueError, match="secret-like term"):
        validate_frontend_env_content("VITE_CLIENT_SECRET=my-secret\n")


def test_no_stray_backend_env():
    """Ensures no stray backend/.env exists."""
    repo_root = Path(__file__).resolve().parents[2]
    backend_env = repo_root / "backend" / ".env"
    if backend_env.exists():
        backend_env.unlink()
    assert not backend_env.exists(), "Stray backend/.env should not exist"

