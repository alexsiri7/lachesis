from __future__ import annotations

from pathlib import Path

from acceptance_internals import app_modules, internal_imports

INTERNAL = {"db", "tools", "main"}


def _imports(tmp_path: Path, source: str) -> list[str]:
    acceptance = tmp_path / "tests" / "acceptance"
    test = acceptance / "backlog" / "test_backlog.py"
    test.parent.mkdir(parents=True)
    test.write_text(source)
    return internal_imports(test, INTERNAL, acceptance)


def test_harness_sdk_and_stdlib_imports_are_allowed(tmp_path: Path) -> None:
    source = (
        "import json\nimport pytest\nfrom mcp.types import TextContent\n"
        "from harness import scenario\n"
    )
    assert _imports(tmp_path, source) == []


def test_app_module_imports_are_named(tmp_path: Path) -> None:
    source = (
        "import db\nfrom tools.requirements import list_requirements\ndef f():\n    import main\n"
    )
    assert sorted(_imports(tmp_path, source)) == ["db", "main", "tools.requirements"]


def test_relative_imports_leaving_acceptance_tests_are_named(tmp_path: Path) -> None:
    assert _imports(tmp_path, "from . import helpers\nfrom ... import conftest\n") == ["..."]


def test_app_modules_are_the_app_directory_modules(tmp_path: Path) -> None:
    (tmp_path / "db.py").write_text("")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "specs.py").write_text("")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "page.html").write_text("")
    (tmp_path / "Dockerfile").write_text("")
    assert app_modules(tmp_path) == {"db", "tools"}
