"""Architecture tests enforcing Project Rules (No Offline Mode).

Guarantees:
1. Production runtime code (backend/app) never imports test doubles, fakes, or tests.
2. Production code contains no forbidden identifiers (DEMO_MODE, dry_run, demo_mode, offline).
3. No Settings class in production declares demo_mode, offline_mode, or dry_run fields.
4. Eliminated demo and fake modules do not exist in backend/app.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

from pydantic_settings import BaseSettings

APP_DIR = Path(__file__).resolve().parent.parent / "app"
FORBIDDEN_IDENTIFIERS = {"demo_mode", "dry_run", "offline", "demo"}


class ArchitectureVisitor(ast.NodeVisitor):
    """AST visitor scanning for test/fake imports and forbidden identifiers."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.import_violations: list[str] = []
        self.identifier_violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name_low = alias.name.lower()
            if "tests" in name_low.split(".") or "fakes" in name_low.split("."):
                self.import_violations.append(
                    f"{self.file_path}:{node.lineno}: import {alias.name}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = (node.module or "").lower()
        parts = mod.split(".")
        if "tests" in parts or "fakes" in parts:
            self.import_violations.append(
                f"{self.file_path}:{node.lineno}: from {node.module} import ..."
            )
        for alias in node.names:
            name_low = alias.name.lower()
            if "fakes" in name_low or "fakemodel" in name_low:
                self.import_violations.append(
                    f"{self.file_path}:{node.lineno}: imported name {alias.name}"
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        id_low = node.id.lower()
        if id_low in FORBIDDEN_IDENTIFIERS:
            self.identifier_violations.append(
                f"{self.file_path}:{node.lineno}: identifier '{node.id}'"
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr_low = node.attr.lower()
        if attr_low in FORBIDDEN_IDENTIFIERS:
            self.identifier_violations.append(
                f"{self.file_path}:{node.lineno}: attribute '{node.attr}'"
            )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        name_low = node.name.lower()
        if name_low in FORBIDDEN_IDENTIFIERS:
            self.identifier_violations.append(
                f"{self.file_path}:{node.lineno}: function '{node.name}'"
            )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        name_low = node.name.lower()
        if name_low in FORBIDDEN_IDENTIFIERS:
            self.identifier_violations.append(
                f"{self.file_path}:{node.lineno}: async function '{node.name}'"
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        name_low = node.name.lower()
        if name_low in FORBIDDEN_IDENTIFIERS:
            self.identifier_violations.append(
                f"{self.file_path}:{node.lineno}: class '{node.name}'"
            )
        self.generic_visit(node)


class TestArchitectureRules:
    """Automated architecture constraints enforcing No Offline Mode."""

    def test_eliminated_modules_do_not_exist(self) -> None:
        demo_py = APP_DIR / "orchestrator" / "demo.py"
        fakes_py = APP_DIR / "agents" / "fakes.py"
        assert not demo_py.exists(), f"Forbidden file exists: {demo_py}"
        assert not fakes_py.exists(), f"Forbidden file exists: {fakes_py}"

    def test_no_imports_of_fakes_or_tests_in_app(self) -> None:
        all_violations: list[str] = []
        for py_file in APP_DIR.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            visitor = ArchitectureVisitor(py_file)
            visitor.visit(tree)
            all_violations.extend(visitor.import_violations)

        assert not all_violations, (
            "Production app code imports test doubles or test modules:\n"
            + "\n".join(all_violations)
        )

    def test_no_forbidden_identifiers_in_app(self) -> None:
        all_violations: list[str] = []
        for py_file in APP_DIR.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            visitor = ArchitectureVisitor(py_file)
            visitor.visit(tree)
            all_violations.extend(visitor.identifier_violations)

        assert not all_violations, (
            "Production app code contains forbidden offline/demo identifiers:\n"
            + "\n".join(all_violations)
        )

    def test_no_settings_class_declares_offline_or_demo_fields(self) -> None:
        settings_classes: list[type[BaseSettings]] = []
        import app

        for module_info in pkgutil.walk_packages(app.__path__, app.__name__ + "."):
            try:
                mod = importlib.import_module(module_info.name)
            except Exception:  # noqa: BLE001, S112
                continue
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseSettings)
                    and attr is not BaseSettings
                ):
                    settings_classes.append(attr)

        assert len(settings_classes) >= 3, "Expected at least 3 settings classes in app"

        violations: list[str] = []
        forbidden_field_substrings = ("demo", "dry_run", "offline")
        for cls in settings_classes:
            for field_name in cls.model_fields:
                field_low = field_name.lower()
                for sub in forbidden_field_substrings:
                    if sub in field_low:
                        violations.append(
                            f"{cls.__module__}.{cls.__name__} has forbidden field '{field_name}'"
                        )

        assert not violations, (
            "Found forbidden offline/demo fields on Settings classes:\n" + "\n".join(violations)
        )
