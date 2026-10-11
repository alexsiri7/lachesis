from __future__ import annotations

import json

import pytest

from scenario_results import check_run, scenario_results


def test_every_spec_scenario_gets_a_result() -> None:
    scenarios = {"backlog": ["A", "B", "C", "D"], "access": ["E"]}
    outcomes = {"backlog": {"A": "passed", "B": "failed", "C": "xfail"}, "gone": {"X": "passed"}}

    assert scenario_results(scenarios, outcomes) == {
        "backlog": {"A": "passed", "B": "failed", "C": "xfail", "D": "untested"},
        "access": {"E": "untested"},
    }


def test_an_unknown_outcome_is_refused() -> None:
    with pytest.raises(ValueError, match="skipped"):
        scenario_results({"backlog": ["A"]}, {"backlog": {"A": "skipped"}})


def test_the_check_run_text_is_the_results_json() -> None:
    results = {"backlog": {"A": "passed", "B": "untested"}}

    run = check_run("abc123", results, 0)

    assert run["name"] == "scenario-results"
    assert run["head_sha"] == "abc123"
    assert run["conclusion"] == "success"
    assert json.loads(run["output"]["text"]) == results  # type: ignore[index]
    assert run["output"]["summary"] == "1 passed, 1 untested"  # type: ignore[index]


def test_a_failed_scenario_fails_the_check_run() -> None:
    assert check_run("abc123", {"backlog": {"A": "failed"}}, 1)["conclusion"] == "failure"


def test_a_failed_run_with_no_failed_scenario_fails_the_check_run() -> None:
    run = check_run("abc123", {"backlog": {"A": "untested"}}, 1)

    assert run["conclusion"] == "failure"
    assert run["output"]["summary"] == (  # type: ignore[index]
        "1 untested; the acceptance run failed (exit code 1)"
    )
