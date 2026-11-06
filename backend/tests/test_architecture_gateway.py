"""Architecture test ensuring zero hardcoded gateway endpoints, proprietary models, or TLS disabling."""

from __future__ import annotations

import re
from pathlib import Path


def test_no_hardcoded_gateway_hosts_or_models():
    """backend/app and frontend/src must not hardcode specific corporate gateway hosts or custom models."""
    repo_root = Path(__file__).resolve().parents[2]
    dirs_to_check = [
        repo_root / "backend" / "app",
        repo_root / "frontend" / "src",
    ]

    forbidden_patterns = [
        re.compile(r"gpt-6-luna", re.IGNORECASE),
        re.compile(
            r"https?://[a-zA-Z0-9.-]*gateway[a-zA-Z0-9.-]*\.(?:corp|internal|com)", re.IGNORECASE
        ),
    ]

    violations = []
    for base_dir in dirs_to_check:
        for file_path in base_dir.rglob("*"):
            if not file_path.is_file():
                continue
            if file_path.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                continue
            # Skip test files and test utilities
            name = file_path.name
            if ".test." in name or "_test." in name or "test_" in name:
                continue
            if "test" in file_path.parts:
                continue

            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for pattern in forbidden_patterns:
                if pattern.search(content):
                    violations.append(
                        f"{file_path.relative_to(repo_root)} matches {pattern.pattern}"
                    )

    assert not violations, f"Forbidden hardcoded gateway host or model found: {violations}"


def test_no_tls_verification_disabled():
    """Verify that verify=False is never used in httpx or requests clients."""
    repo_root = Path(__file__).resolve().parents[2]
    backend_app = repo_root / "backend" / "app"

    violations = []
    for file_path in backend_app.rglob("*.py"):
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"verify\s*=\s*False", content, re.IGNORECASE):
            violations.append(str(file_path.relative_to(repo_root)))

    assert not violations, f"TLS verification disabled (verify=False) in: {violations}"
