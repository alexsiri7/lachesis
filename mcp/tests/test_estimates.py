"""Tests for mcp/tools/estimates.py — the price of a point from past issues, and estimates."""

from __future__ import annotations

from typing import Literal
from unittest.mock import AsyncMock, patch

import pytest

from auth import current_db_user_id
from github import IssueSummary
from spec_reference import SpecReference, append_reference
from tools.estimates import (
    PastIssue,
    estimate_issue,
    estimate_tokens,
    issue_kind,
    past_issues,
    price_per_point,
)

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


def _past(
    number: int, tokens: int, size: int, kind: str = "implementation", model: str = "claude"
) -> PastIssue:
    return PastIssue("alexsiri7/lachesis", number, kind, model, tokens, size)


def test_eight_past_issues_of_the_kind_and_model_give_a_normal_confidence_price() -> None:
    """Scenario: Estimate from points — 2 per point, eight closed, 5 points is 10."""
    past = [_past(n, 10, 5) for n in range(1, 9)]

    price = price_per_point(past, "implementation", "claude")

    assert price == {"tokens_per_point": 2, "confidence": "normal", "basis": "kind", "samples": 8}
    assert estimate_tokens(5, price) == 10


def test_the_price_is_the_tokens_of_its_issues_over_their_points() -> None:
    past = [_past(1, 30, 1), _past(2, 30, 2), _past(3, 30, 3), _past(4, 30, 5), _past(5, 30, 1)]

    assert price_per_point(past, "implementation")["tokens_per_point"] == 150 / 12


def test_below_five_of_the_kind_and_model_the_price_is_across_all_kinds_at_low_confidence() -> None:
    past = [
        *(_past(n, 4, 1) for n in range(1, 5)),
        _past(5, 4, 1, model="claude-sonnet"),
        _past(6, 16, 1, kind="tests"),
    ]

    price = price_per_point(past, "implementation", "claude")

    assert price == {
        "tokens_per_point": 6,
        "confidence": "low",
        "basis": "all kinds",
        "samples": 6,
    }


def test_without_a_model_every_model_of_the_kind_counts() -> None:
    past = [
        *(_past(n, 4, 1) for n in range(1, 4)),
        *(_past(n, 4, 1, model="claude-x") for n in (4, 5)),
    ]

    price = price_per_point(past, "implementation")

    assert price["confidence"] == "normal"
    assert price["samples"] == 5


def test_with_no_history_there_is_no_price_or_estimate() -> None:
    price = price_per_point([], "implementation", "claude")

    assert price == {"tokens_per_point": None, "confidence": "low", "basis": "none", "samples": 0}
    assert estimate_tokens(5, price) is None


@pytest.mark.parametrize("size", [None, 21])
def test_an_unsized_issue_or_one_too_big_to_plan_has_no_estimate(size: int | None) -> None:
    assert estimate_tokens(size, price_per_point([_past(1, 4, 1)])) is None


def test_past_issues_are_closed_sized_issues_with_usage_by_their_main_kind_and_model() -> None:
    issues = [
        _issue(1),
        _issue(2, state="open"),
        _issue(3, pr=True),
        _issue(4),
        _issue(5),
        _issue(6),
    ]
    usage = {n: {"implementation": 100, "tests": 300} for n in (1, 2, 3, 4, 6)}
    models = {n: {"claude": 300, "claude-sonnet": 100} for n in (1, 2, 3, 4, 6)}
    sizes = {1: 2, 2: 2, 3: 2, 5: 2, 6: 21}

    past = past_issues("alexsiri7/lachesis", issues, usage, models, sizes)

    assert past == [PastIssue("alexsiri7/lachesis", 1, "tests", "claude", 400, 2)]


def test_a_past_issue_is_the_kind_that_used_most_tokens_with_ties_in_kind_order() -> None:
    assert issue_kind({"triage": 10, "implementation": 50}) == "implementation"
    assert issue_kind({"tests": 10, "triage": 10}) == "triage"


async def test_estimate_issue_prices_the_issues_size_from_every_registered_repo(
    set_user_id: str, registered: None
) -> None:
    issues = [_issue(230, state="open", capability="sprints")] + [
        _issue(n, capability="sprints") for n in range(1, 4)
    ]
    usage = {n: {"implementation": n * 1000} for n in range(1, 4)}
    models = {n: {"claude": n * 1000} for n in range(1, 4)}
    sizes = {("alexsiri7/lachesis", n): 1 for n in range(1, 4)} | {("alexsiri7/lachesis", 230): 3}
    stored = {"repo_name": "alexsiri7/lachesis", "issue": 230, "tokens": 6000}
    with (
        patch("tools.estimates.github.list_all_issues", AsyncMock(return_value=issues)),
        patch(
            "tools.estimates.db.usage_by_issue",
            side_effect=lambda _u, _r, by="kind": models if by == "model" else usage,
        ),
        patch("tools.estimates.db.set_estimate", return_value=stored) as set_estimate,
        patch("tools.estimates.db.sizes_by_issue", return_value=sizes),
    ):
        result = await estimate_issue("lachesis", 230, "implementation", "claude")

    assert result == {
        "repo_name": "alexsiri7/lachesis",
        "issue": 230,
        "model": "claude",
        "size": 3,
        "estimate_tokens": 6000,
        "tokens_per_point": 2000,
    }
    set_estimate.assert_called_once_with(
        "test-uid",
        "alexsiri7/lachesis",
        230,
        "implementation",
        "sprints",
        6000,
        "low",
        "all kinds",
        3,
    )


async def test_estimate_issue_leaves_out_a_repo_whose_issues_cannot_be_listed(
    set_user_id: str,
) -> None:
    repos = _REGISTERED + [{"id": "r2", "repo_name": "alexsiri7/other", "created_at": "t"}]
    issues = [_issue(230, state="open"), *(_issue(n) for n in range(1, 6))]

    async def listing(repo: str) -> list[IssueSummary]:
        if repo == "alexsiri7/other":
            raise RuntimeError("GitHub is down")
        return issues

    with (
        patch("tools.requirements.db.list_repos", return_value=repos),
        patch("tools.estimates.github.list_all_issues", AsyncMock(side_effect=listing)),
        patch(
            "tools.estimates.db.usage_by_issue",
            side_effect=lambda _u, _r, by="kind": {
                n: {"claude" if by == "model" else "tests": 8} for n in range(1, 6)
            },
        ),
        patch("tools.estimates.db.set_estimate", side_effect=lambda *a: {"tokens": a[5]}),
        patch(
            "tools.estimates.db.sizes_by_issue",
            return_value={("alexsiri7/lachesis", n): 2 for n in range(1, 6)},
        ),
    ):
        result = await estimate_issue("lachesis", 230, "tests")

    assert result["tokens_per_point"] == 4
    assert result["estimate_tokens"] is None


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
        patch("tools.estimates.db.sizes_by_issue", return_value={}),
        pytest.raises(ValueError, match=match),
    ):
        await estimate_issue("lachesis", issue, kind)  # type: ignore[arg-type]

    set_estimate.assert_not_called()
