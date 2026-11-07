"""Architecture test verifying strict boundaries and zero legacy ML dependencies in backend/app."""

from __future__ import annotations

import ast
import re
from pathlib import Path


def test_zero_legacy_ml_imports_in_app() -> None:
    """Verifies nothing under backend/app imports sentence_transformers, transformers, torch, or tests."""
    repo_root = Path(__file__).resolve().parents[2]
    app_dir = repo_root / "backend" / "app"
    assert app_dir.is_dir(), f"Expected app dir at {app_dir}"

    forbidden_modules = {"sentence_transformers", "transformers", "torch"}
    violations: list[str] = []

    for py_file in app_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text, filename=str(py_file))
        except SyntaxError as exc:
            violations.append(f"{py_file.name}: syntax error {exc}")
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".")[0]
                    if root_mod in forbidden_modules:
                        violations.append(f"{py_file.name}:{node.lineno} imports forbidden '{alias.name}'")
                    if root_mod in ("tests", "backend"):
                        violations.append(f"{py_file.name}:{node.lineno} imports from '{alias.name}'")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root_mod = node.module.split(".")[0]
                if root_mod in forbidden_modules:
                    violations.append(f"{py_file.name}:{node.lineno} imports from forbidden '{node.module}'")
                if root_mod in ("tests", "backend"):
                    violations.append(f"{py_file.name}:{node.lineno} imports from '{node.module}'")

    assert not violations, "Found forbidden imports in backend/app:\n" + "\n".join(violations)


def test_zero_legacy_identifiers_in_app() -> None:
    """Verifies nothing under backend/app contains identifiers all-MiniLM, SentenceTransformer, or HF_HUB."""
    repo_root = Path(__file__).resolve().parents[2]
    app_dir = repo_root / "backend" / "app"

    forbidden_patterns = [
        re.compile(r"all-MiniLM", re.IGNORECASE),
        re.compile(r"\bSentenceTransformer\b"),
        re.compile(r"\bHF_HUB\b"),
    ]

    violations: list[str] = []

    for py_file in app_dir.rglob("*.py"):
        lines = py_file.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, 1):
            # Skip comments or deprecation warnings that explain migration
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in forbidden_patterns:
                if pat.search(line):
                    violations.append(f"{py_file.name}:{idx} matches {pat.pattern}: {stripped}")

    assert not violations, "Found forbidden legacy identifiers in backend/app:\n" + "\n".join(violations)
