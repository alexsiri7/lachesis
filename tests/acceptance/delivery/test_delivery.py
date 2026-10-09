"""Delivery: the checks CI runs, run as CI runs them against fixture repositories.

Each test builds a throwaway repository holding this repository's own ``ci/`` scripts,
acceptance harness and coverage settings, plus a fixture spec and fixture acceptance tests,
then runs the check the way the workflows do and reads its exit code and output.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from harness import scenario

ROOT = Path(__file__).resolve().parents[3]
ACCEPTANCE = Path("tests/acceptance")

SPEC = """\
# Widgets

## Purpose

Widgets.

## Requirements

### Requirement: Widgets exist

The system SHALL keep widgets.

#### Scenario: Widget kept
- GIVEN a widget
- WHEN it is stored
- THEN it is kept
"""

KEPT_TEST = """\
from harness import scenario


@scenario("widgets", "Widget kept")
def test_widget_kept() -> None:
    assert 1 + 1 == 2
"""


def _repo(tmp_path: Path, *, spec: str = SPEC, tests: dict[str, str] | None = None) -> Path:
    """A repository with this one's checks and harness, the fixture spec and tests."""
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "ci", repo / "ci", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    shutil.copytree(
        ROOT / ACCEPTANCE / "harness",
        repo / ACCEPTANCE / "harness",
        ignore=shutil.ignore_patterns("test_*.py", "__pycache__"),
    )
    for name in ("conftest.py", "pytest.ini", "coverage.yml"):
        shutil.copy(ROOT / ACCEPTANCE / name, repo / ACCEPTANCE / name)
    (repo / "mcp").mkdir()
    (repo / "mcp" / "widgets.py").write_text("KEPT = True\n")
    (repo / "openspec" / "specs" / "widgets").mkdir(parents=True)
    (repo / "openspec" / "specs" / "widgets" / "spec.md").write_text(spec)
    for path, text in (tests or {"widgets/test_widgets.py": KEPT_TEST}).items():
        target = repo / ACCEPTANCE / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return repo


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=repo, capture_output=True, text=True, timeout=120
    )


def _acceptance_run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """The acceptance step of CI: pytest over the acceptance tests."""
    return _run(repo, "-m", "pytest", str(ACCEPTANCE), "-p", "no:cacheprovider", *args)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _goalposts(repo: Path, base: str, head: str) -> subprocess.CompletedProcess[str]:
    """The goalposts check on an implementation pull request: no labels, no linked issue."""
    return _run(repo, "ci/goalposts.py", f"--base={base}", f"--head={head}", "--labels=[]")


def _pull_request(tmp_path: Path, test_before: str, test_after: str) -> tuple[Path, str, str]:
    repo = _repo(tmp_path, tests={"widgets/test_widgets.py": test_before})
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "implementation")
    (repo / ACCEPTANCE / "widgets" / "test_widgets.py").write_text(test_after)
    (repo / "mcp" / "widgets.py").write_text("KEPT = 'yes'\n")
    head = _commit(repo, "implementation")
    return repo, base, head


@pytest.mark.xfail(strict=True, reason="spec change #173")
@scenario("delivery", "Scenario without a test")
def test_scenario_without_a_test(tmp_path: Path) -> None:
    spec = SPEC + "\n#### Scenario: Widget lost\n- GIVEN a widget\n- WHEN it is lost\n- THEN no\n"
    repo = _repo(tmp_path, spec=spec)

    result = _run(repo, "ci/scenario_coverage.py")

    assert result.returncode != 0
    assert "widgets" in result.stdout
    assert "Widget lost" in result.stdout


@pytest.mark.xfail(strict=True, reason="spec change #173")
@scenario("delivery", "Test for a deleted scenario")
def test_test_for_a_deleted_scenario(tmp_path: Path) -> None:
    orphan = KEPT_TEST + (
        '\n\n@scenario("widgets", "Widget melted")\ndef test_widget_melted() -> None:\n'
        "    assert True\n"
    )
    repo = _repo(tmp_path, tests={"widgets/test_widgets.py": orphan})

    result = _run(repo, "ci/scenario_coverage.py")

    assert result.returncode != 0
    assert "test_widget_melted" in result.stdout


@scenario("delivery", "Test reaching into internals")
def test_test_reaching_into_internals(tmp_path: Path) -> None:
    reaching = "import widgets\n\n" + KEPT_TEST
    repo = _repo(tmp_path, tests={"widgets/test_widgets.py": reaching})

    result = _run(repo, "ci/acceptance_internals.py")

    assert result.returncode != 0
    assert "test_widgets.py" in result.stdout


@scenario("delivery", "New scenario lands red")
def test_new_scenario_lands_red(tmp_path: Path) -> None:
    red = (
        "import pytest\n\nfrom harness import scenario\n\n\n"
        '@pytest.mark.xfail(strict=True, reason="spec change #1")\n'
        '@scenario("widgets", "Widget kept")\n'
        "def test_widget_kept() -> None:\n"
        "    assert False, 'not built yet'\n"
    )
    repo = _repo(tmp_path, tests={"widgets/test_widgets.py": red})

    result = _acceptance_run(repo)

    assert result.returncode == 0, result.stdout
    assert "1 xfailed" in result.stdout


@scenario("delivery", "Behaviour arrives")
def test_behaviour_arrives(tmp_path: Path) -> None:
    arrived = (
        "import pytest\n\nfrom harness import scenario\n\n\n"
        '@pytest.mark.xfail(strict=True, reason="spec change #1")\n'
        '@scenario("widgets", "Widget kept")\n'
        "def test_widget_kept() -> None:\n"
        "    assert True\n"
    )
    repo = _repo(tmp_path, tests={"widgets/test_widgets.py": arrived})

    result = _acceptance_run(repo)

    assert result.returncode != 0
    assert "remove its expected-failure marker" in result.stdout


@scenario("delivery", "Implementation edits an assertion")
def test_implementation_edits_an_assertion(tmp_path: Path) -> None:
    edited = KEPT_TEST.replace("assert 1 + 1 == 2", "assert 1 + 1 in (2, 3)")
    repo, base, head = _pull_request(tmp_path, KEPT_TEST, edited)

    result = _goalposts(repo, base, head)

    assert result.returncode != 0
    assert "test_widget_kept" in result.stdout


@scenario("delivery", "Implementation removes a marker")
def test_implementation_removes_a_marker(tmp_path: Path) -> None:
    marked = KEPT_TEST.replace(
        "from harness import scenario\n",
        "import pytest\n\nfrom harness import scenario\n",
    ).replace(
        '@scenario("widgets"',
        '@pytest.mark.xfail(strict=True, reason="spec change #1")\n@scenario("widgets"',
    )
    repo, base, head = _pull_request(tmp_path, marked, KEPT_TEST)

    goalposts = _goalposts(repo, base, head)
    acceptance = _acceptance_run(repo)

    assert goalposts.returncode == 0, goalposts.stdout
    assert acceptance.returncode == 0, acceptance.stdout


@scenario("delivery", "After a merge")
def test_after_a_merge(tmp_path: Path) -> None:
    spec = SPEC + "\n#### Scenario: Widget lost\n- GIVEN a widget\n- WHEN it is lost\n- THEN no\n"
    lost = KEPT_TEST + (
        '\n\n@pytest.mark.xfail(strict=True, reason="spec change #1")\n'
        '@scenario("widgets", "Widget lost")\ndef test_widget_lost() -> None:\n'
        "    assert False\n"
    )
    lost = "import pytest\n\n" + lost
    repo = _repo(tmp_path, spec=spec, tests={"widgets/test_widgets.py": lost})
    outcomes = tmp_path / "outcomes.json"

    run = _acceptance_run(repo, "--scenario-results", str(outcomes))
    published = _run(
        repo,
        "ci/scenario_results.py",
        "--sha=abc123",
        f"--outcomes={outcomes}",
        f"--exit-code={run.returncode}",
    )

    assert published.returncode == 0, published.stderr
    check_run = json.loads(published.stdout)
    assert check_run["head_sha"] == "abc123"
    results = json.loads(check_run["output"]["text"])
    assert results == {"widgets": {"Widget kept": "passed", "Widget lost": "xfail"}}
