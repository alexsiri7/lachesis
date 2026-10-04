"""Tests for mcp/tools/estimates.py — token estimates from the history of past issues."""

from __future__ import annotations

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest

from auth import current_db_user_id
from github import IssueSummary
from spec_reference import SpecReference, append_reference
from tools.estimates import estimate_from_history, estimate_issue, issue_kind

_REGISTERED = [{"id": "r1", "repo_name": "alexsiri7/lachesis", "created_at": "t"}]


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def registered():
    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        yield


def _issue(
    number: int,
    state: Literal["open", "closed"] = "closed",
    capability: str | None = None,
    pr: bool = False,
) -> IssueSummary:
    body = "x" if capability is None else append_reference("x", SpecReference(226, capability))
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/alexsiri7/lachesis/issues/{number}",
        "body": body,
        "state": state,
        "labels": [],
        "is_pull_request": pr,
        "merged": False,
        "created_at": None,
        "updated_at": None,
    }


def test_eight_past_implementation_issues_in_the_capability_give_a_normal_confidence_estimate() -> (
    None
):
    """Scenario: eight past implementation issues in backlog — derived and normal confidence."""
    issues = [_issue(n, capability="backlog") for n in range(1, 9)]
    usage = {n: {"implementation": n * 1000, "triage": 100} for n in range(1, 9)}

    estimate = estimate_from_history(20, "implementation", "backlog", issues, usage)

    assert estimate == {
        "tokens": 4600,
        "confidence": "normal",
        "basis": "capability",
        "samples": 8,
    }


def test_fewer_than_five_matching_issues_are_low_confidence() -> None:
    issues = [_issue(n, capability="backlog") for n in range(1, 5)]
    usage = {n: {"implementation": 1000} for n in range(1, 5)}

    estimate = estimate_from_history(20, "implementation", "backlog", issues, usage)

    assert estimate["confidence"] == "low"
    assert estimate["basis"] == "capability"
    assert estimate["samples"] == 4


def test_history_ignores_open_issues_pull_requests_the_target_other_kinds_and_capabilities() -> (
    None
):
    issues = [
        _issue(1, capability="backlog"),
        _issue(2, state="open", capability="backlog"),
        _issue(3, capability="backlog", pr=True),
        _issue(20, capability="backlog"),
        _issue(4, capability="backlog"),
        _issue(5, capability="fuel"),
        _issue(6, capability="backlog"),
    ]
    usage = {
        1: {"implementation": 700},
        2: {"implementation": 9000},
        3: {"implementation": 9000},
        20: {"implementation": 9000},
        4: {"implementation": 100, "tests": 9000},
        5: {"implementation": 9000},
    }

    estimate = estimate_from_history(20, "implementation", "backlog", issues, usage)

    assert estimate["samples"] == 1
    assert estimate["tokens"] == 700


def test_a_past_issue_is_the_kind_that_used_most_tokens_with_ties_in_kind_order() -> None:
    assert issue_kind({"triage": 10, "implementation": 50}) == "implementation"
    assert issue_kind({"tests": 10, "triage": 10}) == "triage"


def test_with_no_history_in_the_capability_it_falls_back_to_the_kind_at_low_confidence() -> None:
    issues = [_issue(n, capability="fuel") for n in range(1, 7)]
    usage = {n: {"implementation": 1000} for n in range(1, 7)}

    estimate = estimate_from_history(20, "implementation", "backlog", issues, usage)

    assert estimate == {"tokens": 1000, "confidence": "low", "basis": "kind", "samples": 6}


def test_with_no_history_tokens_is_none() -> None:
    estimate = estimate_from_history(20, "implementation", "backlog", [_issue(1)], {})

    assert estimate == {"tokens": None, "confidence": "low", "basis": "none", "samples": 0}


def test_issues_without_a_reference_block_match_each_other() -> None:
    issues = [_issue(1), _issue(2, capability="backlog")]
    usage = {1: {"tests": 300}, 2: {"tests": 900}}

    estimate = estimate_from_history(20, "tests", None, issues, usage)

    assert estimate == {"tokens": 300, "confidence": "low", "basis": "capability", "samples": 1}


async def test_estimate_issue_stores_the_estimate_with_the_issue(
    set_user_id: str, registered: None
) -> None:
    issues = [_issue(230, state="open", capability="sprints")] + [
        _issue(n, capability="sprints") for n in range(1, 4)
    ]
    usage = {n: {"implementation": n * 1000} for n in range(1, 4)}
    stored = {"repo_name": "alexsiri7/lachesis", "issue": 230, "tokens": 2000}
    with (
        patch("tools.estimates.github.list_all_issues", AsyncMock(return_value=issues)),
        patch("tools.estimates.db.usage_by_issue", return_value=usage) as usage_by_issue,
        patch("tools.estimates.db.set_estimate", return_value=stored) as set_estimate,
    ):
        result = await estimate_issue("lachesis", 230, "implementation")

    assert result == stored
    usage_by_issue.assert_called_once_with("test-uid", "alexsiri7/lachesis")
    set_estimate.assert_called_once_with(
        "test-uid",
        "alexsiri7/lachesis",
        230,
        "implementation",
        "sprints",
        2000,
        "low",
        "capability",
        3,
    )


@pytest.mark.parametrize(
    ("issue", "kind", "match"),
    [
        (0, "implementation", "issue"),
        (230, "coding", "kind must be one of"),
        (999, "implementation", "not found"),
        (7, "implementation", "not found"),
    ],
)
async def test_estimate_issue_refuses_bad_input(
    set_user_id: str, registered: None, issue: int, kind: str, match: str
) -> None:
    issues = [_issue(230, state="open"), _issue(7, pr=True)]
    with (
        patch("tools.estimates.github.list_all_issues", AsyncMock(return_value=issues)),
        patch("tools.estimates.db.usage_by_issue", return_value={}),
        patch("tools.estimates.db.set_estimate") as set_estimate,
        pytest.raises(ValueError, match=match),
    ):
        await estimate_issue("lachesis", issue, kind)  # type: ignore[arg-type]

    set_estimate.assert_not_called()
