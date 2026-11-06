"""Unit tests for CLI --check-env diagnostics and fail-fast key validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app.agents.cli import _handle_check_env, main
from app.agents.settings import LLMSettings


def test_check_env_output_diagnostics(capsys: pytest.CaptureFixture[str]):
    """--check-env correctly reports .env path, key status, model, and mode."""
    settings = LLMSettings(
        llm_model="gpt-4o-mini",
        openai_api_key=None,
        openai_base_url="https://test.local/v1",
        llm_api_mode="chat_completions",
    )
    _handle_check_env(settings, do_ping=False)
    captured = capsys.readouterr().out

    assert "ENVIRONMENT & CONFIG CHECK" in captured
    assert ".env Path:" in captured
    assert "gpt-4o-mini" in captured
    assert "chat_completions" in captured
    assert "https://test.local/v1" in captured


def test_check_env_ping_skipped_when_no_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """--check-env --ping reports SKIPPED when no valid API key is present."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    settings = LLMSettings(openai_api_key=None)
    _handle_check_env(settings, do_ping=True)
    captured = capsys.readouterr().out

    assert "SKIPPED (No valid API key configured to ping)" in captured


def test_cli_live_fail_fast_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    """Running CLI when key is missing stops with exit code 1 and helpful instructions."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr("app.agents.settings.ENV_FILE_PATH", tmp_path / ".env")
    monkeypatch.setattr("app.agents.cli.ENV_FILE_PATH", tmp_path / ".env")
    test_args = ["cli.py", "--example", "sheet1"]
    monkeypatch.setattr(sys, "argv", test_args)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    err_output = capsys.readouterr().err
    assert "Error: No valid OpenAI API key is configured." in err_output
    assert "Where looked:" in err_output
    assert "PowerShell steps to create .env file:" in err_output


def test_cli_live_fail_fast_when_key_is_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    """Running CLI when key is a placeholder fails fast."""
    monkeypatch.setenv("OPENAI_API_KEY", "your-key-here")
    monkeypatch.setattr("app.agents.settings.ENV_FILE_PATH", tmp_path / ".env")
    monkeypatch.setattr("app.agents.cli.ENV_FILE_PATH", tmp_path / ".env")
    test_args = ["cli.py", "--example", "sheet1"]
    monkeypatch.setattr(sys, "argv", test_args)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    err_output = capsys.readouterr().err
    assert "Error: No valid OpenAI API key is configured." in err_output
    assert "obvious placeholder" in err_output


def test_check_env_prints_variable_names_origins_and_key_length_only(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    """--check-env prints which variable supplied key & base URL, file/OS origin, and length only."""
    fake_secret = "sk-corp-super-long-token-9876543210-abcdef"
    env_file = tmp_path / "custom.env"
    env_file.write_text(f"OPENAI_API_KEY={fake_secret}\nOPENAI_BASE_URL=https://gateway.company.com/v1\n", encoding="utf-8")

    settings = LLMSettings(_env_file=env_file)  # type: ignore[call-arg]
    _handle_check_env(settings, do_ping=False)
    out = capsys.readouterr().out

    # 1. Variable name checks
    assert "Key Variable:       OPENAI_API_KEY" in out
    assert "Base URL Variable:  OPENAI_BASE_URL" in out

    # 2. Origin checks
    assert "Key Origin:" in out
    assert str(env_file) in out
    assert "Base URL Origin:" in out
    assert str(env_file) in out

    # 3. Key length only check (never secret characters)
    assert f"Key Length:         {len(fake_secret)} characters" in out
    assert fake_secret not in out
    assert "sk-corp" not in out
    assert "9876543210" not in out


def test_cli_check_env_conflicting_settings_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    """Running CLI with --check-env when settings conflict fails fast naming variables (never values)."""
    from app.agents.settings import get_llm_settings

    get_llm_settings.cache_clear()

    monkeypatch.setenv("OPENAI_API_KEY", "key-one-11111")
    monkeypatch.setenv("LLM_API_KEY", "key-two-22222")

    test_args = ["cli.py", "--check-env"]
    monkeypatch.setattr(sys, "argv", test_args)

    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 1
    err_output = capsys.readouterr().err
    assert "Conflicting settings: both OPENAI_API_KEY and LLM_API_KEY are set with different values" in err_output
    assert "key-one-11111" not in err_output
    assert "key-two-22222" not in err_output

