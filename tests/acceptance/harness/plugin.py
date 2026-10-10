"""Pytest wiring: the ``lachesis`` fixture, the strict-xfail message and scenario results."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from harness.client import Lachesis
from harness.server import RunningApp, run_app

# Worst outcome wins when a test reports from several phases.
_RANK = {"skipped": 0, "passed": 1, "xfail": 2, "failed": 3}
_OUTCOMES = pytest.StashKey[dict[tuple[str, str], str]]()


@pytest.fixture(scope="session")
def _app() -> Iterator[RunningApp]:
    with run_app() as app:
        yield app


@pytest.fixture
def lachesis(_app: RunningApp) -> Lachesis:
    _app.github.reset()
    _app.ntfy.reset()
    _app.database.reset()
    return Lachesis(_app.base_url, _app.token, _app.github, _app.ntfy)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--scenario-results",
        metavar="PATH",
        help="write each claimed scenario's outcome as JSON: capability -> title -> outcome",
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    report: pytest.TestReport = yield
    if report.failed and str(report.longrepr).startswith("[XPASS(strict)]"):
        report.longrepr = (
            f"{report.longrepr}\nThis scenario now passes: remove its expected-failure marker."
        )
    marker = item.get_closest_marker("scenario")
    if marker is not None and (report.when == "call" or not report.passed):
        outcome = "xfail" if hasattr(report, "wasxfail") and report.skipped else report.outcome
        outcomes = item.config.stash.setdefault(_OUTCOMES, {})
        key = (str(marker.args[0]), str(marker.args[1]))
        if _RANK[outcome] >= _RANK.get(outcomes.get(key, "skipped"), 0):
            outcomes[key] = outcome
    return report


def pytest_sessionfinish(session: pytest.Session) -> None:
    path = session.config.getoption("--scenario-results")
    if not path:
        return
    results: dict[str, dict[str, str]] = {}
    for (capability, title), outcome in session.config.stash.get(_OUTCOMES, {}).items():
        if outcome != "skipped":
            results.setdefault(capability, {})[title] = outcome
    Path(path).write_text(json.dumps(results, indent=2, sort_keys=True))
