"""Unit tests for agent settings, model factory, and Pydantic v2 strict models."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agents import AgentOutputSchema, OpenAIChatCompletionsModel, OpenAIResponsesModel
from app.agents.model_factory import build_model
from app.agents.models import SAPAnalysis, SQLAnalysis
from app.agents.settings import (
    ENV_FILE_PATH,
    LLMSettings,
    clean_api_key,
    get_key_status,
    is_valid_api_key,
)
from pydantic import SecretStr, ValidationError


def test_settings_switching_model_and_base_url():
    """Switching LLM_MODEL, OPENAI_BASE_URL, or LLM_API_MODE builds appropriate model without code changes."""
    # 1. Default responses mode
    s1 = LLMSettings(
        llm_model="gpt-4o-mini",
        llm_api_mode="responses",
        openai_api_key=SecretStr("sk-test-fake-key-12345"),
    )
    m1 = build_model(s1)
    assert isinstance(m1, OpenAIResponsesModel)

    # 2. Chat completions mode explicitly
    s2 = LLMSettings(
        llm_model="gpt-4o",
        llm_api_mode="chat_completions",
        openai_api_key=SecretStr("sk-test-fake-key-12345"),
    )
    m2 = build_model(s2)
    assert isinstance(m2, OpenAIChatCompletionsModel)

    # 3. Base URL sets default to chat_completions
    s3 = LLMSettings(
        llm_model="custom-local-model",
        openai_base_url="http://localhost:8000/v1",
        openai_api_key=SecretStr("sk-test-fake-key-12345"),
    )
    assert s3.llm_api_mode == "chat_completions"
    m3 = build_model(s3)
    assert isinstance(m3, OpenAIChatCompletionsModel)


def test_api_key_never_logged_or_in_repr():
    """OpenAI API key is a SecretStr and never exposed in repr or str."""
    secret = "sk-super-secret-key-12345"
    s = LLMSettings(openai_api_key=SecretStr(secret))
    s_repr = repr(s)
    s_str = str(s)
    assert secret not in s_repr
    assert secret not in s_str
    assert "**********" in s_repr


def test_strict_schema_conversion_for_analysis_models():
    """SQLAnalysis and SAPAnalysis schemas export successfully with SDK strict schema conversion."""
    sql_schema = AgentOutputSchema(SQLAnalysis, strict_json_schema=True)
    assert sql_schema is not None
    assert sql_schema._output_schema["additionalProperties"] is False

    sap_schema = AgentOutputSchema(SAPAnalysis, strict_json_schema=True)
    assert sap_schema is not None
    assert sap_schema._output_schema["additionalProperties"] is False


def test_extra_fields_forbidden():
    """Models forbid extra fields (extra='forbid')."""
    with pytest.raises(ValidationError):
        SQLAnalysis(
            query_purpose="Test",
            statement_type="SELECT",
            technical_summary="Summary",
            unrecognized_extra_field="disallowed",  # type: ignore[call-arg]
        )

    with pytest.raises(ValidationError):
        SAPAnalysis(
            sap_module="MM",
            client_handling="Standard",
            unrecognized_extra_field="disallowed",  # type: ignore[call-arg]
        )


def test_placeholder_api_keys_treated_as_not_set():
    """Blank values and obvious placeholders are treated as NOT SET (None)."""
    placeholders = [
        "",
        "   ",
        "sk-...",
        "sk-xxx",
        "your-key-here",
        "YOUR-KEY-HERE",
        "changeme",
        "dummy",
        "none",
        "null",
        "placeholder",
    ]
    for p in placeholders:
        assert clean_api_key(p) is None, f"Failed to clean placeholder: {p}"
        assert is_valid_api_key(p) is False
        s = LLMSettings(openai_api_key=p)  # type: ignore[arg-type]
        assert s.openai_api_key is None, f"Placeholder {p} was not set to None"


def test_os_env_overrides_dotenv(tmp_path: Path):
    """OS environment variables strictly take precedence over .env file."""
    # Create temporary .env at repo root path
    test_env = tmp_path / ".env"
    test_env.write_text("OPENAI_API_KEY=sk-proj-file-key-from-dotenv-99999\n", encoding="utf-8")

    orig_env = os.environ.get("OPENAI_API_KEY")
    try:
        # Set OS env var
        os.environ["OPENAI_API_KEY"] = "sk-proj-os-key-priority-11111"
        s = LLMSettings(_env_file=test_env)  # type: ignore[call-arg]
        assert s.openai_api_key is not None
        assert s.openai_api_key.get_secret_value() == "sk-proj-os-key-priority-11111"
    finally:
        if orig_env is not None:
            os.environ["OPENAI_API_KEY"] = orig_env
        else:
            os.environ.pop("OPENAI_API_KEY", None)


def test_settings_loads_from_repo_root_regardless_of_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Settings resolves .env relative to repo root regardless of CWD."""
    # Change working directory to a sub-folder
    sub_dir = tmp_path / "some_nested_working_dir"
    sub_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(sub_dir)

    status = get_key_status()
    # The resolved env_path must point to MDM_Syniti root .env, not cwd/.env
    assert status["env_path"] == ENV_FILE_PATH


def test_llm_base_url_and_api_key_aliases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """LLM_BASE_URL and LLM_API_KEY work as deprecated aliases and emit deprecation warnings."""
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")

    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.internal/v1")
    monkeypatch.setenv("LLM_API_KEY", "gateway-token-xyz-12345")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.deprecated_call():
        s = LLMSettings(_env_file=empty_env)  # type: ignore[call-arg]

    assert s.openai_base_url == "https://gateway.internal/v1"
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == "gateway-token-xyz-12345"
    assert s.llm_api_mode == "chat_completions"
    assert s.key_variable == "LLM_API_KEY (deprecated)"
    assert s.base_url_variable == "LLM_BASE_URL (deprecated)"


def test_conflicting_api_keys_fail_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setting OPENAI_API_KEY and LLM_API_KEY with different values fails fast naming both variables (never values)."""
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")

    # 1. Conflict in constructor kwargs
    with pytest.raises(ValueError) as exc1:
        LLMSettings(
            openai_api_key="key-alpha-111",  # type: ignore[arg-type]
            llm_api_key="key-beta-222",  # type: ignore[call-arg]
            _env_file=empty_env,  # type: ignore[call-arg]
        )
    err1 = str(exc1.value)
    assert "both OPENAI_API_KEY and LLM_API_KEY are set with different values" in err1
    assert "key-alpha-111" not in err1
    assert "key-beta-222" not in err1

    # 2. Conflict in OS environment
    monkeypatch.setenv("OPENAI_API_KEY", "key-gamma-333")
    monkeypatch.setenv("LLM_API_KEY", "key-delta-444")
    with pytest.raises(ValueError) as exc2:
        LLMSettings(_env_file=empty_env)  # type: ignore[call-arg]
    err2 = str(exc2.value)
    assert "both OPENAI_API_KEY and LLM_API_KEY are set with different values" in err2
    assert "key-gamma-333" not in err2
    assert "key-delta-444" not in err2

    # 3. Conflict in .env file
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    conflict_env = tmp_path / "conflict.env"
    conflict_env.write_text("OPENAI_API_KEY=key-epsilon-555\nLLM_API_KEY=key-zeta-666\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc3:
        LLMSettings(_env_file=conflict_env)  # type: ignore[call-arg]
    err3 = str(exc3.value)
    assert "both OPENAI_API_KEY and LLM_API_KEY are set with different values" in err3
    assert "key-epsilon-555" not in err3
    assert "key-zeta-666" not in err3


def test_conflicting_base_urls_fail_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setting OPENAI_BASE_URL and LLM_BASE_URL with different values fails fast naming both variables (never values)."""
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")

    # 1. Conflict in constructor kwargs
    with pytest.raises(ValueError) as exc1:
        LLMSettings(
            openai_base_url="https://gateway-a.internal/v1",
            llm_base_url="https://gateway-b.internal/v1",  # type: ignore[call-arg]
            openai_api_key="valid-dummy-key",  # type: ignore[arg-type]
            _env_file=empty_env,  # type: ignore[call-arg]
        )
    err1 = str(exc1.value)
    assert "both OPENAI_BASE_URL and LLM_BASE_URL are set with different values" in err1

    # 2. Conflict in OS environment
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway-c.internal/v1")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway-d.internal/v1")
    with pytest.raises(ValueError) as exc2:
        LLMSettings(openai_api_key="valid-dummy-key", _env_file=empty_env)  # type: ignore[arg-type, call-arg]
    err2 = str(exc2.value)
    assert "both OPENAI_BASE_URL and LLM_BASE_URL are set with different values" in err2


def test_identical_canonical_and_alias_succeeds_without_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Setting canonical and alias with IDENTICAL values succeeds with no error."""
    empty_env = tmp_path / ".env"
    empty_env.write_text("", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "identical-key-12345")
    monkeypatch.setenv("LLM_API_KEY", "identical-key-12345")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.internal/v1")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.internal/v1")

    s = LLMSettings(_env_file=empty_env)  # type: ignore[call-arg]
    assert s.openai_api_key is not None
    assert s.openai_api_key.get_secret_value() == "identical-key-12345"
    assert s.openai_base_url == "https://gateway.internal/v1"
    assert s.key_variable == "OPENAI_API_KEY"
    assert s.base_url_variable == "OPENAI_BASE_URL"


