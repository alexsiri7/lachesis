"""Tests for mcp/tools/scorecard.py — the model scorecard and trust rules."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from auth import current_db_user_id
from tools.scorecard import read_scorecard, report_run_outcome, scorecard, trust_level

_REGISTERED = [{"id": "r1", "repo_name": "alexsiri7/lachesis", "created_at": "t"}]
_REPO = "alexsiri7/lachesis"
_KINDS = ["triage", "summarising", "audit", "tests", "implementation", "spec work"]


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def registered():
    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        yield


def _run(
    issue: int,
    kind: str = "triage",
    model: str = "deepseek/deepseek-chat",
    *,
    rework: int = 0,
    retries: int = 0,
    audit_findings: int = 0,
    tokens_in: int = 100,
    tokens_out: int = 10,
    cost_usd: float | None = None,
    repo_name: str = _REPO,
    size: int | None = None,
) -> dict[str, Any]:
    return {
        "repo_name": repo_name,
        "size": size,
        "issue": issue,
        "model": model,
        "kind": kind,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "rework": rework,
        "retries": retries,
        "audit_findings": audit_findings,
    }


def _kind(models: list[dict[str, Any]], model: str, kind: str) -> dict[str, Any]:
    entry = next(m for m in models if m["model"] == model)
    return next(k for k in entry["kinds"] if k["kind"] == kind)


def _issue(number: int, state: str = "closed", is_pull_request: bool = False) -> dict:
    return {"number": number, "state": state, "is_pull_request": is_pull_request}


def test_reading_the_scorecard() -> None:
    """Scenario: Reading the scorecard — per model and kind: runs, merges, failures, cost."""
    runs = [
        _run(1, cost_usd=0.10, tokens_in=1000, tokens_out=50),
        _run(2, cost_usd=0.25, retries=1),
        _run(3, kind="implementation", rework=1, cost_usd=1.5),
        _run(4, kind="implementation", audit_findings=2, cost_usd=0.004),
        _run(5, kind="implementation"),
        _run(1, model="claude-opus-5-5", tokens_in=7, tokens_out=3),
    ]

    models = scorecard(runs, {_REPO: {1, 2, 3, 4}})

    assert [m["model"] for m in models] == ["claude-opus-5-5", "deepseek/deepseek-chat"]
    for m in models:
        assert [k["kind"] for k in m["kinds"]] == _KINDS
    triage = _kind(models, "deepseek/deepseek-chat", "triage")
    assert triage["runs"] == 2
    assert triage["merged_without_rework"] == 2
    assert triage["retries"] == 1
    assert triage["audit_findings"] == 0
    assert (triage["tokens_in"], triage["tokens_out"]) == (1100, 60)
    assert triage["cost_usd"] == 0.35
    implementation = _kind(models, "deepseek/deepseek-chat", "implementation")
    assert implementation["runs"] == 3
    assert implementation["merged_without_rework"] == 1
    assert implementation["retries"] == 0
    assert implementation["audit_findings"] == 2
    assert implementation["cost_usd"] == 1.5
    allowance = _kind(models, "claude-opus-5-5", "triage")
    assert allowance["cost_usd"] == 0.0
    assert _kind(models, "claude-opus-5-5", "tests") == {
        "kind": "tests",
        "runs": 0,
        "merged_without_rework": 0,
        "retries": 0,
        "audit_findings": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "tokens_per_point": None,
        "cost_per_point": None,
        "trusted": False,
        "trusted_up_to": None,
        "sizes": [],
        "may_trial": False,
    }


def test_each_size_is_scored_per_point_delivered() -> None:
    """Scenario: Reading the scorecard — per size: tokens and cost per point delivered."""
    runs = [
        _run(1, size=3, tokens_in=50, tokens_out=40, cost_usd=0.03),
        _run(2, size=3, tokens_in=50, tokens_out=40, cost_usd=0.03, rework=1),
        _run(3, size=3, tokens_in=50, tokens_out=40, cost_usd=0.03),
        _run(4, size=1, tokens_in=5, tokens_out=5),
        _run(5),
    ]

    triage = _kind(scorecard(runs, {_REPO: {1, 2, 4, 5}}), "deepseek/deepseek-chat", "triage")

    # The open issue 3 delivered no points, but its tokens and cost still count.
    assert triage["sizes"] == [
        {
            "size": 1,
            "runs": 1,
            "merged_without_rework": 1,
            "retries": 0,
            "audit_findings": 0,
            "tokens": 10,
            "cost_usd": 0.0,
            "tokens_per_point": 10.0,
            "cost_per_point": 0.0,
            "trusted": False,
        },
        {
            "size": 3,
            "runs": 3,
            "merged_without_rework": 1,
            "retries": 0,
            "audit_findings": 0,
            "tokens": 270,
            "cost_usd": 0.09,
            "tokens_per_point": 45.0,
            "cost_per_point": 0.015,
            "trusted": False,
        },
    ]
    assert triage["runs"] == 5
    assert triage["tokens_per_point"] == 40.0


def _sized(size: int | None, first: int, count: int = 5, **failure: int) -> list[dict[str, Any]]:
    return [_run(n, size=size, **failure) for n in range(first, first + count)]


def test_trust_at_a_size_covers_every_smaller_size() -> None:
    """Scenario: Stepping up a size — trust at 3 points covers 1 and 2, not 5."""
    closed = {_REPO: set(range(1, 20))}
    runs = _sized(1, 1, 2, rework=1) + _sized(3, 10)

    triage = _kind(scorecard(runs, closed), "deepseek/deepseek-chat", "triage")

    assert triage["trusted_up_to"] == 3
    assert [(s["size"], s["trusted"]) for s in triage["sizes"]] == [(1, True), (3, True)]
    assert triage["trusted"]


def test_runs_at_a_smaller_size_do_not_earn_trust_at_a_larger_one() -> None:
    closed = {_REPO: set(range(1, 20))}
    runs = _sized(1, 1, 3) + _sized(5, 10, 2)

    triage = _kind(scorecard(runs, closed), "deepseek/deepseek-chat", "triage")

    assert triage["trusted_up_to"] == 1
    assert [(s["size"], s["trusted"]) for s in triage["sizes"]] == [(1, True), (5, False)]


def test_losing_trust_at_a_size() -> None:
    """Scenario: Losing trust — two failures at a size lose trust at every size."""
    closed = {_REPO: set(range(1, 20))}
    runs = _sized(2, 1) + _sized(2, 10, 2, rework=1)

    triage = _kind(scorecard(runs, closed), "deepseek/deepseek-chat", "triage")

    assert triage["trusted_up_to"] is None
    assert not triage["trusted"]


def test_failures_at_a_smaller_size_lose_the_trust_a_larger_one_gave_it() -> None:
    """Scenario: Losing trust — clean runs at 3 points do not hide failures at 1."""
    closed = {_REPO: set(range(1, 20))}
    runs = _sized(3, 1) + _sized(1, 10, 2, rework=1)

    triage = _kind(scorecard(runs, closed), "deepseek/deepseek-chat", "triage")

    assert triage["trusted_up_to"] is None
    assert not triage["trusted"]


def test_failures_at_a_larger_size_leave_trust_at_a_smaller_one() -> None:
    closed = {_REPO: set(range(1, 20))}
    runs = _sized(2, 1) + _sized(3, 10, 2, rework=1)

    triage = _kind(scorecard(runs, closed), "deepseek/deepseek-chat", "triage")

    assert triage["trusted_up_to"] == 2


def test_runs_on_issues_never_sized_earn_trust_at_one_point() -> None:
    closed = {_REPO: set(range(1, 6))}

    triage = _kind(scorecard(_sized(None, 1), closed), "deepseek/deepseek-chat", "triage")

    assert (triage["trusted"], triage["trusted_up_to"], triage["sizes"]) == (True, 1, [])


def test_earning_trust() -> None:
    """Scenario: Earning trust — five consecutive clean runs of a kind."""
    four = [_run(n) for n in range(1, 5)]
    closed = {_REPO: set(range(1, 6))}

    assert not _kind(scorecard(four, closed), "deepseek/deepseek-chat", "triage")["trusted"]
    assert _kind(scorecard([*four, _run(5)], closed), "deepseek/deepseek-chat", "triage")["trusted"]


@pytest.mark.parametrize("failure", [{"retries": 1}, {"audit_findings": 1}, {"rework": 1}])
def test_a_failure_in_the_streak_starts_it_again(failure: dict[str, int]) -> None:
    runs = [_run(n) for n in range(1, 5)] + [_run(5, **failure)]
    runs += [_run(n) for n in range(6, 10)]

    models = scorecard(runs, {_REPO: set(range(1, 10))})

    assert not _kind(models, "deepseek/deepseek-chat", "triage")["trusted"]


def test_a_run_on_an_open_issue_neither_counts_nor_breaks_the_streak() -> None:
    runs = [_run(n) for n in range(1, 6)]
    closed_but_3 = {1, 2, 4, 5, 6}

    models = scorecard(runs, {_REPO: closed_but_3})
    assert not _kind(models, "deepseek/deepseek-chat", "triage")["trusted"]

    models = scorecard([*runs, _run(6)], {_REPO: closed_but_3})
    assert _kind(models, "deepseek/deepseek-chat", "triage")["trusted"]


def test_merged_is_scoped_to_each_runs_own_repository() -> None:
    other = "alexsiri7/kindred"
    runs = [_run(1, repo_name=_REPO), _run(1, repo_name=other)]

    models = scorecard(runs, {_REPO: {1}, other: set()})

    assert _kind(models, "deepseek/deepseek-chat", "triage")["merged_without_rework"] == 1


def test_a_failure_on_an_open_issue_is_judged() -> None:
    runs = [_run(n) for n in range(1, 5)] + [_run(5, retries=1)] + [_run(n) for n in range(6, 10)]

    models = scorecard(runs, {_REPO: {1, 2, 3, 4, 6, 7, 8, 9}})

    assert not _kind(models, "deepseek/deepseek-chat", "triage")["trusted"]


def test_losing_trust() -> None:
    """Scenario: Losing trust — two failures in the last five runs of the kind."""
    earned = [_run(n, kind="tests") for n in range(1, 6)]
    closed = {_REPO: set(range(1, 11))}

    one_failure = [*earned, _run(6, kind="tests", rework=1), _run(7, kind="tests")]
    assert _kind(scorecard(one_failure, closed), "deepseek/deepseek-chat", "tests")["trusted"]

    two_failures = [*one_failure, _run(8, kind="tests", rework=1)]
    assert not _kind(scorecard(two_failures, closed), "deepseek/deepseek-chat", "tests")["trusted"]


@pytest.mark.parametrize(
    ("judged", "trusted"),
    [
        ([], False),
        ([True] * 5, True),
        ([True] * 5 + [False, True, True, True, False], False),
        ([True] * 5 + [False, True, True, True, True, False], True),
        ([True] * 5 + [False, False] + [True] * 4, False),
        ([True] * 5 + [False, False] + [True] * 5, True),
    ],
)
def test_trust_level_replays_the_rules(judged: list[bool], trusted: bool) -> None:
    assert (trust_level([(1, clean) for clean in judged]) is not None) is trusted


def _may_trial(runs: list[dict[str, Any]]) -> dict[str, bool]:
    closed = {_REPO: {run["issue"] for run in runs}}
    entry = scorecard(runs, closed)[0]
    return {k["kind"]: k["may_trial"] for k in entry["kinds"]}


def test_a_new_model_may_trial_triage_and_summarising_only() -> None:
    assert _may_trial([_run(1, kind="tests")]) == {
        "triage": True,
        "summarising": True,
        "audit": False,
        "tests": False,
        "implementation": False,
        "spec work": False,
    }


def test_trust_on_triage_opens_every_kind_to_trial() -> None:
    assert all(_may_trial([_run(n) for n in range(1, 6)]).values())


def test_trust_on_tests_opens_only_the_kinds_above_it() -> None:
    assert _may_trial([_run(n, kind="tests") for n in range(1, 6)]) == {
        "triage": True,
        "summarising": True,
        "audit": False,
        "tests": True,
        "implementation": True,
        "spec work": True,
    }


async def test_report_run_outcome_stores_it_against_the_resolved_repository(
    set_user_id: str, registered: None
) -> None:
    stored = {"id": 1, "run_id": 7, "model": "deepseek/deepseek-chat", "outcome": "rework"}
    with patch("tools.scorecard.db.record_run_outcome", return_value=stored) as record:
        result = await report_run_outcome("lachesis", 160, "implementation", "rework")

    assert result == stored
    record.assert_called_once_with("test-uid", _REPO, 160, "implementation", "rework")


@pytest.mark.parametrize(
    ("issue", "kind", "outcome", "match"),
    [
        (0, "tests", "retry", "must name an issue"),
        (160, "coding", "retry", "kind must be one of"),
        (160, "tests", "merged", "outcome must be one of"),
    ],
)
async def test_report_run_outcome_refuses_bad_input(
    set_user_id: str, registered: None, issue: int, kind: str, outcome: str, match: str
) -> None:
    with (
        patch("tools.scorecard.db.record_run_outcome") as record,
        pytest.raises(ValueError, match=match),
    ):
        await report_run_outcome("lachesis", issue, kind, outcome)  # type: ignore[arg-type]

    record.assert_not_called()


async def test_read_scorecard_lists_each_repository_with_runs_once(set_user_id: str) -> None:
    other = "alexsiri7/kindred"
    runs = [_run(1), _run(2), _run(3), _run(1, repo_name=other)]
    listings = {
        _REPO: [_issue(1), _issue(2, is_pull_request=True), _issue(3, state="open")],
        other: [_issue(1)],
    }
    with (
        patch("tools.scorecard.db.list_runs", return_value=runs) as list_runs,
        patch(
            "tools.scorecard.github.list_all_issues",
            new=AsyncMock(side_effect=lambda repo: listings[repo]),
        ) as list_all_issues,
    ):
        result = await read_scorecard()

    list_runs.assert_called_once_with("test-uid")
    assert sorted(c.args[0] for c in list_all_issues.call_args_list) == [other, _REPO]
    assert _kind(result["models"], "deepseek/deepseek-chat", "triage")["runs"] == 4
    assert _kind(result["models"], "deepseek/deepseek-chat", "triage")["merged_without_rework"] == 2


async def test_read_scorecard_with_no_runs_reads_nothing_from_github(set_user_id: str) -> None:
    with (
        patch("tools.scorecard.db.list_runs", return_value=[]),
        patch("tools.scorecard.github.list_all_issues", new=AsyncMock()) as list_all_issues,
    ):
        assert await read_scorecard() == {"models": []}

    list_all_issues.assert_not_called()
