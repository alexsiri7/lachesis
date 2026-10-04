"""Fail an implementation pull request that changes acceptance tests beyond removing xfail markers.

A pull request labelled ``spec-change``, or one that closes an issue labelled
``acceptance-tests``, may change acceptance tests freely. Any other may only remove
expected-failure markers (and the imports that removal leaves unused).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable

from acceptance import ACCEPTANCE_DIR, REPO_ROOT, definitions

ACCEPTANCE_PREFIX = f"{ACCEPTANCE_DIR.relative_to(REPO_ROOT).as_posix()}/"
SPEC_CHANGE_LABEL = "spec-change"
ACCEPTANCE_TESTS_LABEL = "acceptance-tests"

# GitHub's closing keywords, e.g. "Closes #174".
_CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+#(\d+)", re.IGNORECASE)


def closed_issues(body: str) -> list[int]:
    return [int(n) for n in _CLOSES.findall(body)]


def may_change_acceptance_tests(
    labels: Iterable[str], body: str, issue_labels: Callable[[int], Iterable[str]]
) -> bool:
    if SPEC_CHANGE_LABEL in labels:
        return True
    return any(ACCEPTANCE_TESTS_LABEL in issue_labels(n) for n in closed_issues(body))


def _is_xfail(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
    return name == "xfail"


def _without_xfails_and_imports(tree: ast.Module) -> tuple[ast.Module, Counter[str], Counter[str]]:
    """Strip xfail decorators and top-level imports, returning what was stripped."""
    markers: Counter[str] = Counter()
    for qualname, node in definitions(tree):
        kept = []
        for decorator in node.decorator_list:
            if _is_xfail(decorator):
                markers[f"{qualname} {ast.dump(decorator)}"] += 1
            else:
                kept.append(decorator)
        node.decorator_list = kept
    imports = Counter(ast.dump(n) for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom))
    tree.body = [n for n in tree.body if not isinstance(n, ast.Import | ast.ImportFrom)]
    return tree, markers, imports


def _definition_dumps(tree: ast.Module) -> dict[str, str]:
    return {qualname: ast.dump(node) for qualname, node in definitions(tree)}


def changed_tests(path: str, old: str, new: str) -> list[str]:
    """Name what changed in *path* beyond removed xfail markers; empty when nothing did."""
    try:
        old_tree, old_markers, old_imports = _without_xfails_and_imports(ast.parse(old))
        new_tree, new_markers, new_imports = _without_xfails_and_imports(ast.parse(new))
    except SyntaxError:
        return [path]
    if (
        ast.dump(old_tree) == ast.dump(new_tree)
        and not new_markers - old_markers
        and not new_imports - old_imports
    ):
        return []
    old_defs, new_defs = _definition_dumps(old_tree), _definition_dumps(new_tree)
    names = {
        name
        for name in old_defs.keys() | new_defs.keys()
        if old_defs.get(name) != new_defs.get(name)
    }
    names |= {marker.split(" ", 1)[0] for marker in new_markers - old_markers}
    # A changed method also changes its class; name the method alone.
    names = {n for n in names if not any(other.startswith(f"{n}::") for other in names)}
    return [f"{path}::{name}" for name in sorted(names)] or [path]


def violations(changes: Iterable[tuple[str, str, str | None, str | None]]) -> list[str]:
    """Each change is ``(status, path, old, new)`` with git's A/M/D status letter."""
    found = []
    for status, path, old, new in changes:
        if not path.startswith(ACCEPTANCE_PREFIX):
            continue
        if status != "M" or old is None or new is None or not path.endswith(".py"):
            found.append(path)
        else:
            found += changed_tests(path, old, new)
    return found


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _git_changes(base: str, head: str) -> list[tuple[str, str, str | None, str | None]]:
    changes = []
    diff = _git(
        "diff",
        "--no-renames",
        "--name-status",
        f"{base}...{head}",
        "--",
        f":(top){ACCEPTANCE_PREFIX}",
    )
    for line in diff.splitlines():
        status, path = line.split("\t", 1)
        old = _git("show", f"{base}:{path}") if status in ("M", "D") else None
        new = _git("show", f"{head}:{path}") if status in ("M", "A") else None
        changes.append((status[0], path, old, new))
    return changes


def _issue_labels(number: int) -> list[str]:
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{os.environ['GITHUB_REPOSITORY']}/issues/{number}",
            "--jq",
            "[.labels[].name]",
        ],
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout) if result.returncode == 0 else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--labels", default="[]", help="the pull request's labels, as a JSON list")
    parser.add_argument("--body", default="", help="the pull request's body")
    args = parser.parse_args()

    if may_change_acceptance_tests(json.loads(args.labels), args.body, _issue_labels):
        print("Spec change or acceptance-tests issue: acceptance tests may change.")
        return 0
    found = violations(_git_changes(args.base, args.head))
    for test in found:
        print(f"Acceptance test changed by an implementation pull request: {test}")
    if found:
        print("Implementations may only remove expected-failure markers from acceptance tests.")
        return 1
    print("Acceptance tests unchanged apart from removed expected-failure markers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
