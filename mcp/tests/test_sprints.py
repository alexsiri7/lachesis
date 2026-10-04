"""Tests for mcp/tools/sprints.py — the week's budget and the plan that fills it."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import main
from auth import current_db_user_id
from settings import settings
from tools.sprints import plan_sprint, read_sprint, sprint_budget

_RESETS_AT = datetime(2026, 10, 11, 9, tzinfo=UTC)
_NOW = _RESETS_AT - timedelta(days=2)


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


def _fuel(
    used: float = 40.0,
    spent: float = 0.5,
    cap: float = 1.0,
    resets_at: datetime = _RESETS_AT,
) -> dict[str, Any]:
    return {
        "weekly_used_percent": used,
        "weekly_resets_at": resets_at,
        "shorter_limit_until": None,
        "overflow_spent_usd": spent,
        "overflow_cap_usd": cap,
        "reported_at": resets_at - timedelta(days=3),
    }


def _entry(number: int, *, ready: bool = True, labels: list[str] | None = None) -> dict:
    return {
        "repo": "owner/repo",
        "number": number,
        "title": f"Issue {number}",
        "tier": "other",
        "ready": ready,
        "labels": labels or [],
    }


def _estimates(tokens: dict[int, int | None]) -> dict[tuple[str, int], int | None]:
    return {("owner/repo", number): value for number, value in tokens.items()}


# ---------------------------------------------------------------------------
# sprint_budget
# ---------------------------------------------------------------------------


def test_no_fuel_means_no_sprint() -> None:
    assert sprint_budget(None, _NOW, 1_000_000, 5.0) is None


def test_the_budget_is_the_allowance_outside_the_reserve_and_the_overflow_cap() -> None:
    budget = sprint_budget(_fuel(), _NOW, 1_000_000, 5.0)

    assert budget is not None
    assert budget["start"] == _RESETS_AT - timedelta(days=7)
    assert budget["end"] == _RESETS_AT
    assert budget["allowance"]["budget_tokens"] == 800_000
    assert budget["overflow"]["budget_tokens"] == 200_000
    assert budget["budget_tokens"] == 1_000_000
    assert budget["spent_tokens"] == 500_000
    assert budget["left_tokens"] == 500_000
    assert budget["within_pace"] is True


def test_the_reserve_is_never_counted_as_spendable() -> None:
    budget = sprint_budget(_fuel(used=90.0, spent=1.0), _NOW, 1_000_000, 5.0)

    assert budget is not None
    assert budget["spent_tokens"] == budget["budget_tokens"]
    assert budget["left_tokens"] == 0


def test_a_reported_week_that_has_reset_leaves_the_whole_budget() -> None:
    now = _RESETS_AT + timedelta(days=1)
    budget = sprint_budget(_fuel(used=90.0, spent=1.0), now, 1_000_000, 5.0)

    assert budget is not None
    assert budget["start"] == _RESETS_AT
    assert budget["spent_tokens"] == 0
    assert budget["left_tokens"] == budget["budget_tokens"]
    assert budget["within_pace"] is None


# ---------------------------------------------------------------------------
# plan_sprint
# ---------------------------------------------------------------------------


def test_plan_fills_the_budget() -> None:
    """Scenario: Plan fills the budget — 100 left, issues of 40, 40 and 30."""
    plan, not_planned = plan_sprint(
        [_entry(1), _entry(2), _entry(3)], _estimates({1: 40, 2: 40, 3: 30}), 100
    )

    assert [item["number"] for item in plan] == [1, 2]
    assert not_planned == 1


def test_plan_goes_on_to_smaller_work_that_still_fits() -> None:
    plan, not_planned = plan_sprint(
        [_entry(1), _entry(2), _entry(3), _entry(4)],
        _estimates({1: 40, 2: 40, 3: 30, 4: 20}),
        100,
    )

    assert [item["number"] for item in plan] == [1, 2, 4]
    assert not_planned == 1


def test_plan_includes_unestimated_work_and_never_waits_on_the_author() -> None:
    entries = [_entry(1, ready=False), _entry(2, labels=["needs-author"]), _entry(3)]

    plan, not_planned = plan_sprint(entries, {}, 100)

    assert plan == [
        {
            "repo": "owner/repo",
            "number": 3,
            "title": "Issue 3",
            "tier": "other",
            "estimate_tokens": None,
        }
    ]
    assert not_planned == 0


# ---------------------------------------------------------------------------
# read_sprint
# ---------------------------------------------------------------------------


def _open_issue(number: int, created_at: datetime) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "labels": [],
        "body": "",
        "created_at": created_at.isoformat(),
    }


@contextlib.contextmanager
def _two_repos(*, focus: list[dict] | None = None, unreadable: str | None = None):
    """Repos A and B, each with one recent issue estimated at 60 of 100 tokens left."""
    now = datetime.now(UTC)
    open_issues = {
        "owner/a": [_open_issue(1, now - timedelta(hours=2))],
        "owner/b": [_open_issue(2, now - timedelta(hours=1))],
    }

    async def list_open_issues(repo: str) -> list[dict[str, Any]]:
        if repo == unreadable:
            raise RuntimeError("gone")
        return open_issues[repo]

    estimates = [
        {"repo_name": "owner/a", "issue": 1, "tokens": 60},
        {"repo_name": "owner/b", "issue": 2, "tokens": 60},
    ]
    with (
        patch("db.list_repos", return_value=[{"repo_name": "owner/a"}, {"repo_name": "owner/b"}]),
        patch(
            "db.get_fuel",
            return_value=_fuel(used=0.0, spent=0.0, resets_at=now + timedelta(days=3)),
        ),
        patch("db.list_estimates", return_value=estimates),
        patch("db.list_backlog", return_value=[]),
        patch("db.list_milestones", return_value=[]),
        patch("db.list_focus", return_value=focus or []),
        patch("github.list_open_issues", AsyncMock(side_effect=list_open_issues)),
        patch.object(settings, "claude_weekly_allowance_tokens", 125),
        patch.object(settings, "claude_usd_per_million_tokens", 0.0),
    ):
        yield


async def test_read_sprint_plans_the_backlog_in_order(set_user_id: str) -> None:
    with _two_repos():
        result = await read_sprint()

    assert result["reported"] is True
    assert result["left_tokens"] == 100
    assert [(item["repo"], item["number"]) for item in result["plan"]] == [("owner/a", 1)]
    assert result["planned_tokens"] == 60
    assert result["not_planned"] == 1
    assert result["unreadable"] == []


async def test_read_sprint_replans_when_focus_changes(set_user_id: str) -> None:
    """Scenario: Focus changes mid-sprint — the plan is redone from the new order."""
    focus = [{"repo_name": "owner/b", "milestone_id": None}]

    with _two_repos(focus=focus):
        result = await read_sprint()

    assert [(item["repo"], item["number"]) for item in result["plan"]] == [("owner/b", 2)]
    assert result["plan"][0]["tier"] == "focus"


async def test_read_sprint_plans_the_repos_it_can_read(set_user_id: str) -> None:
    with _two_repos(unreadable="owner/a"):
        result = await read_sprint()

    assert result["unreadable"] == ["owner/a"]
    assert [(item["repo"], item["number"]) for item in result["plan"]] == [("owner/b", 2)]


async def test_read_sprint_with_no_fuel_has_no_sprint(set_user_id: str) -> None:
    with patch("db.get_fuel", return_value=None):
        result = await read_sprint()

    assert result["reported"] is False
    assert "report_fuel" in result["explanation"]


async def test_read_sprint_is_registered_read_only() -> None:
    tools = {tool.name: tool for tool in await main.mcp.list_tools()}
    assert tools["read_sprint"].annotations.readOnlyHint is True
