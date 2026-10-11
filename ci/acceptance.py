"""What the delivery checks read: spec scenarios, acceptance tests and the scenarios they claim."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPECS_DIR = REPO_ROOT / "openspec" / "specs"
ACCEPTANCE_DIR = REPO_ROOT / "tests" / "acceptance"
HARNESS_DIR = ACCEPTANCE_DIR / "harness"

_SCENARIO_HEADING = re.compile(r"^#### Scenario:[ \t]*(.*?)[ \t]*$", re.MULTILINE)


Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)
class Claim:
    capability: str
    title: str
    test: str


def scenario_titles(spec: str) -> list[str]:
    """Every scenario title in *spec*, in order.  A heading without a title names no scenario."""
    return [title for title in _SCENARIO_HEADING.findall(spec) if title]


def spec_scenarios(specs_dir: Path = SPECS_DIR) -> dict[str, list[str]]:
    """Map each capability to its scenario titles, in spec order."""
    return {
        spec.parent.name: scenario_titles(spec.read_text())
        for spec in sorted(specs_dir.glob("*/spec.md"))
    }


def acceptance_test_files(acceptance_dir: Path = ACCEPTANCE_DIR) -> list[Path]:
    """Every Python file of the acceptance tests themselves, which excludes the harness."""
    harness = acceptance_dir / "harness"
    return sorted(p for p in acceptance_dir.rglob("*.py") if not p.is_relative_to(harness))


def node_id(path: Path, qualname: str, root: Path = REPO_ROOT) -> str:
    return f"{path.relative_to(root).as_posix()}::{qualname}"


def definitions(tree: ast.Module) -> Iterator[tuple[str, Definition]]:
    """Yield every function and class with its ``Class::method`` style qualified name."""

    def walk(body: list[ast.stmt], prefix: str) -> Iterator[tuple[str, Definition]]:
        for node in body:
            if isinstance(node, Definition):
                qualname = f"{prefix}{node.name}"
                yield qualname, node
                if isinstance(node, ast.ClassDef):
                    yield from walk(node.body, f"{qualname}::")

    yield from walk(tree.body, "")


def _scenario_marker(decorator: ast.expr) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    args = decorator.args
    if name != "scenario" or len(args) != 2:
        return None
    if not all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args):
        return None
    return args[0].value, args[1].value  # type: ignore[attr-defined]


def scenario_claims(acceptance_dir: Path = ACCEPTANCE_DIR, root: Path = REPO_ROOT) -> list[Claim]:
    claims = []
    for path in acceptance_test_files(acceptance_dir):
        for qualname, node in definitions(ast.parse(path.read_text(), str(path))):
            for decorator in node.decorator_list:
                marker = _scenario_marker(decorator)
                if marker is not None:
                    claims.append(Claim(*marker, test=node_id(path, qualname, root)))
    return claims
