"""Fail when an acceptance test imports anything of the codebase outside ``tests/acceptance/``.

The harness is the one place allowed to reach into the app, so it is not checked.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from acceptance import ACCEPTANCE_DIR, REPO_ROOT, acceptance_test_files

APP_DIR = REPO_ROOT / "mcp"


def app_modules(app_dir: Path = APP_DIR) -> set[str]:
    """Top-level names the app's own modules are imported by."""
    return {
        p.stem if p.is_file() else p.name
        for p in app_dir.iterdir()
        if (p.is_file() and p.suffix == ".py") or (p.is_dir() and any(p.rglob("*.py")))
    }


def internal_imports(
    path: Path, internal: set[str], acceptance_dir: Path = ACCEPTANCE_DIR
) -> list[str]:
    """Return the imports in *path* that resolve to the codebase outside *acceptance_dir*."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in internal]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = path.parents[node.level - 1]
                if not package.is_relative_to(acceptance_dir):
                    found.append("." * node.level + (node.module or ""))
            elif node.module and node.module.split(".")[0] in internal:
                found.append(node.module)
    return found


def main() -> int:
    internal = app_modules()
    failed = False
    for path in acceptance_test_files():
        for module in internal_imports(path, internal):
            failed = True
            print(f"{path.relative_to(REPO_ROOT)} imports {module}, outside tests/acceptance/")
    if not failed:
        print("No acceptance test imports the codebase outside tests/acceptance/.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
