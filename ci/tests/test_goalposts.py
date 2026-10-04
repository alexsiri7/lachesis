from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from acceptance import REPO_ROOT
from goalposts import (
    SPEC_CHANGE_LABEL,
    _git_changes,
    closed_issues,
    may_change_acceptance_tests,
    violations,
)

PATH = "tests/acceptance/backlog/test_backlog.py"

RED = dedent(
    """
    import pytest

    from harness import scenario


    @scenario("backlog", "Everything blocked")
    @pytest.mark.xfail(strict=True, reason="spec change #12")
    async def test_everything_blocked(lachesis):
        assert (await lachesis.call_tool("get_backlog")).json() == []


    @scenario("backlog", "Nothing blocked")
    @pytest.mark.xfail(strict=True, reason="spec change #12")
    async def test_nothing_blocked(lachesis):
        assert (await lachesis.call_tool("get_backlog")).json() != []
    """
)


def _modified(old: str, new: str, path: str = PATH) -> list[str]:
    return violations([("M", path, old, new)])


def _no_labels(_: int) -> list[str]:
    return []


def test_removing_an_xfail_marker_is_allowed() -> None:
    green = RED.replace(
        '@pytest.mark.xfail(strict=True, reason="spec change #12")\nasync def test_everything',
        "async def test_everything",
    )
    assert green != RED
    assert _modified(RED, green) == []


def test_removing_every_marker_and_the_import_it_leaves_unused_is_allowed() -> None:
    green = RED.replace('@pytest.mark.xfail(strict=True, reason="spec change #12")\n', "")
    green = green.replace("import pytest\n", "")
    assert _modified(RED, green) == []


def test_reformatting_and_comments_are_not_changes() -> None:
    reformatted = RED.replace("== []", "== [ ]  # still empty")
    assert _modified(RED, reformatted) == []


def test_editing_an_assertion_names_the_test() -> None:
    edited = RED.replace("json() == []", "json() is not None")
    assert _modified(RED, edited) == [f"{PATH}::test_everything_blocked"]


def test_removing_a_marker_while_editing_an_assertion_names_the_test() -> None:
    edited = RED.replace(
        '@pytest.mark.xfail(strict=True, reason="spec change #12")\nasync def test_nothing',
        "async def test_nothing",
    ).replace("json() != []", "json() is not None")
    assert _modified(RED, edited) == [f"{PATH}::test_nothing_blocked"]


def test_adding_an_xfail_marker_names_the_test() -> None:
    green = RED.replace('@pytest.mark.xfail(strict=True, reason="spec change #12")\n', "")
    assert _modified(green, RED) == [
        f"{PATH}::test_everything_blocked",
        f"{PATH}::test_nothing_blocked",
    ]


def test_loosening_a_marker_names_the_test() -> None:
    loose = RED.replace(
        'strict=True, reason="spec change #12")\nasync def test_nothing',
        'strict=False, reason="spec change #12")\nasync def test_nothing',
    )
    assert _modified(RED, loose) == [f"{PATH}::test_nothing_blocked"]


def test_changing_a_scenario_claim_names_the_test() -> None:
    moved = RED.replace('"Nothing blocked"', '"Something blocked"')
    assert _modified(RED, moved) == [f"{PATH}::test_nothing_blocked"]


def test_deleting_a_test_names_it() -> None:
    old = RED
    new = RED[: RED.index('@scenario("backlog", "Nothing blocked")')]
    assert _modified(old, new) == [f"{PATH}::test_nothing_blocked"]


def test_a_method_change_names_the_method() -> None:
    old = "class TestBacklog:\n    def test_a(self):\n        assert 1\n"
    new = "class TestBacklog:\n    def test_a(self):\n        assert 2\n"
    assert _modified(old, new) == [f"{PATH}::TestBacklog::test_a"]


def test_a_module_level_change_names_the_file() -> None:
    assert _modified("X = 1\n", "X = 2\n") == [PATH]


def test_added_and_deleted_files_are_named() -> None:
    assert violations(
        [
            ("A", "tests/acceptance/backlog/test_new.py", None, "def test_x(): ..."),
            ("D", "tests/acceptance/backlog/test_old.py", "def test_x(): ...", None),
        ]
    ) == ["tests/acceptance/backlog/test_new.py", "tests/acceptance/backlog/test_old.py"]


def test_any_change_to_a_non_python_file_is_named() -> None:
    path = "tests/acceptance/coverage.yml"
    assert violations([("M", path, "enforce: false\n", "enforce: true\n")]) == [path]


def test_files_outside_acceptance_tests_are_ignored() -> None:
    assert violations([("M", "mcp/tests/test_db.py", "X = 1", "X = 2")]) == []


def test_closing_keywords_name_the_closed_issues() -> None:
    body = "Closes #174, fixes #3.\nResolves: #9 and mentions #10; closed #11"
    assert closed_issues(body) == [174, 3, 9, 11]


def test_spec_change_label_permits_changes() -> None:
    assert may_change_acceptance_tests(["spec-change"], "", _no_labels)


def test_closing_an_acceptance_tests_issue_permits_changes() -> None:
    def labels(number: int) -> list[str]:
        return ["acceptance-tests"] if number == 174 else []

    assert may_change_acceptance_tests([], "Closes #174", labels)


def test_closing_another_issue_does_not_permit_changes() -> None:
    def labels(_: int) -> list[str]:
        return ["enhancement"]

    assert not may_change_acceptance_tests(["enhancement"], "Closes #12", labels)


def test_mentioning_an_acceptance_tests_issue_does_not_permit_changes() -> None:
    def labels(_: int) -> list[str]:
        return ["acceptance-tests"]

    assert not may_change_acceptance_tests([], "Follows #174", labels)


def test_reads_changes_from_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    test_file = tmp_path / PATH
    test_file.parent.mkdir(parents=True)
    git("init", "-q")
    test_file.write_text(RED)
    git("add", ".")
    git("commit", "-qm", "red")
    base = git("rev-parse", "HEAD")
    test_file.write_text(RED.replace("json() == []", "json() is not None"))
    (tmp_path / "mcp.py").write_text("X = 1\n")
    git("add", ".")
    git("commit", "-qm", "edit")
    monkeypatch.chdir(tmp_path / "tests")

    assert violations(_git_changes(base, "HEAD")) == [f"{PATH}::test_everything_blocked"]


def test_trusts_the_label_the_app_gives_spec_changes() -> None:
    # The checks run without the app's dependencies, so its constant is read, not imported.
    specs = ast.parse((REPO_ROOT / "mcp" / "tools" / "specs.py").read_text())
    app_label = next(
        node.value.value
        for node in specs.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SPEC_CHANGE_LABEL" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    )

    assert app_label == SPEC_CHANGE_LABEL
