"""Tests for mcp/tools/sprints.py — the week's budget and the plan that fills it."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import db
import main
from auth import current_db_user_id
from settings import settings
from tools.estimates import price_per_point
from tools.sprints import (
    capacity_points,
    ended_sprint,
    estimate_accuracy,
    plan_sprint,
    plan_with_improvement,
    read_sprint,
    report_sprint,
    sprint_budget,
    sprint_findings,
    sprint_spending,
)


@pytest.fixture(autouse=True)
def _no_accounts_overflow_or_experiments():
    """No registered accounts, overflow report or open experiments unless a test says
    otherwise."""
    with (
        patch("db.list_fuel", return_value=[]),
        patch("db.get_overflow", return_value=None),
        patch("db.list_open_experiments", return_value=[]),
    ):
        yield


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
            "size": None,
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
def _two_repos(
    *,
    focus: list[dict] | None = None,
    unreadable: str | None = None,
    sizes: dict[tuple[str, int], int] | None = None,
):
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

    async def list_all_issues(repo: str) -> list[dict[str, Any]]:
        if repo == unreadable:
            raise RuntimeError("gone")
        return [{**item, "state": "closed", "is_pull_request": False} for item in closed[repo]]

    # Closed issue 9 in owner/a: 3 points that cost 120 tokens.
    closed = {"owner/a": [_open_issue(9, now - timedelta(days=9))], "owner/b": []}
    usage = {"owner/a": {9: {"implementation": 120}}, "owner/b": {}}
    models = {"owner/a": {9: {"claude": 120}}, "owner/b": {}}

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
        patch("db.sizes_by_issue", return_value={("owner/a", 9): 3, **(sizes or {})}),
        patch(
            "db.usage_by_issue",
            side_effect=lambda _u, repo, by="kind": (models if by == "model" else usage)[repo],
        ),
        patch("db.list_backlog", return_value=[]),
        patch("db.list_milestones", return_value=[]),
        patch("db.list_focus", return_value=focus or []),
        patch("github.list_open_issues", AsyncMock(side_effect=list_open_issues)),
        patch("github.list_all_issues", AsyncMock(side_effect=list_all_issues)),
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


async def test_read_sprint_states_its_capacity_in_points_at_the_price_of_a_point(
    set_user_id: str,
) -> None:
    """Scenario: How far the budget goes — a budget of 100 at 40 per point is 2 points."""
    with _two_repos():
        result = await read_sprint()

    assert result["budget_tokens"] == 100
    assert result["tokens_per_point"] == 40
    assert result["price_confidence"] == "low"
    assert result["capacity_points"] == 2


async def test_read_sprint_counts_the_points_planned_but_not_an_issue_to_split(
    set_user_id: str,
) -> None:
    with _two_repos(sizes={("owner/a", 1): 5, ("owner/b", 2): 21}):
        result = await read_sprint()

    assert [(item["number"], item.get("split", False)) for item in result["plan"]] == [
        (1, False),
        (2, True),
    ]
    assert result["planned_points"] == 5


def test_improvement_work_is_planned_first_within_its_share() -> None:
    """Scenario: Improvement share respected — a budget of 100 gives improvement work 10."""
    plan, not_planned = plan_with_improvement(
        [_entry(1), _entry(2), _entry(3), _entry(4)],
        {("owner/repo", 2), ("owner/repo", 3)},
        _estimates({1: 50, 2: 6, 3: 6, 4: 40}),
        left_tokens=100,
        improvement_tokens=10,
    )

    assert [(item["number"], item.get("improvement", False)) for item in plan] == [
        (2, True),
        (1, False),
        (4, False),
    ]
    assert not_planned == 1


def test_improvement_work_never_takes_more_than_is_left() -> None:
    plan, _ = plan_with_improvement(
        [_entry(1), _entry(2)],
        {("owner/repo", 1)},
        _estimates({1: 8, 2: 1}),
        left_tokens=5,
        improvement_tokens=10,
    )

    assert [item["number"] for item in plan] == [2]


def _experiment_row(issues: list[int]) -> dict[str, Any]:
    return {
        "id": 4,
        "repo_name": "owner/b",
        "title": "Self-review",
        "change": "Review the diff first.",
        "measure": "rework_rate",
        "baseline": 40.0,
        "target": 10.0,
        "issues": issues,
        "state": "open",
        "results": [],
        "created_at": _NOW,
    }


async def test_read_sprint_plans_open_experiments_first_in_their_share(
    set_user_id: str,
) -> None:
    with (
        _two_repos(),
        patch("db.list_open_experiments", return_value=[_experiment_row([2])]),
        patch.object(settings, "improvement_share_percent", 60),
    ):
        result = await read_sprint()

    assert result["improvement_budget_tokens"] == 60
    assert [(item["repo"], item["number"], item.get("improvement")) for item in result["plan"]] == [
        ("owner/b", 2, True)
    ]


def test_with_no_price_there_is_no_capacity() -> None:
    assert capacity_points(100, price_per_point([])) is None


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


# ---------------------------------------------------------------------------
# The ended sprint and its report
# ---------------------------------------------------------------------------

_END = _RESETS_AT - timedelta(days=7)
_START = _END - timedelta(days=7)


def test_the_ended_sprint_is_the_week_before_the_current_one() -> None:
    assert ended_sprint(_fuel(), _NOW) == (_START, _END)


def test_no_fuel_means_no_sprint_has_ended() -> None:
    assert ended_sprint(None, _NOW) is None


def _run(
    issue: int,
    *,
    cost: float | None = None,
    retries: int = 0,
    rework: int = 0,
    repo: str = "owner/repo",
) -> dict[str, Any]:
    return {
        "repo_name": repo,
        "issue": issue,
        "tokens_in": 100,
        "tokens_out": 10,
        "cost_usd": cost,
        "retries": retries,
        "rework": rework,
    }


def test_spending_splits_claude_runs_from_overflow_runs() -> None:
    runs = [_run(1), _run(2), _run(3, cost=0.104), _run(4, cost=0.2)]

    spending = sprint_spending(runs, _fuel(resets_at=_END), _END)

    assert spending["claude"] == {
        "runs": 2,
        "tokens": 220,
        "allowance_used_percent": 40.0,
        "accounts": [{"name": "unnamed", "runs": 2, "tokens": 220}],
    }
    assert spending["overflow"] == {
        "runs": 2,
        "tokens": 220,
        "cost_usd": 0.3,
        "spent_usd": 0.5,
        "cap_usd": 1.0,
    }


def test_spending_leaves_out_a_fuel_report_on_another_week() -> None:
    spending = sprint_spending([], _fuel(resets_at=_RESETS_AT), _END)

    assert spending["claude"]["allowance_used_percent"] is None
    assert spending["overflow"]["spent_usd"] is None
    assert spending["overflow"]["cap_usd"] is None


def _estimate(issue: int, tokens: int | None, capability: str | None = "backlog") -> dict:
    return {
        "repo_name": "owner/repo",
        "issue": issue,
        "kind": "implementation",
        "capability": capability,
        "tokens": tokens,
    }


def test_estimate_accuracy_compares_each_issue_and_each_kind_and_capability() -> None:
    estimates = [_estimate(1, 100), _estimate(2, 300), _estimate(3, 50, capability=None)]
    usage = {"owner/repo": {1: {"implementation": 150, "tests": 50}, 2: {"tests": 100}, 3: {}}}

    accuracy = estimate_accuracy([("owner/repo", n) for n in (1, 2, 3)], estimates, usage)

    assert [(i["number"], i["actual_tokens"], i["ratio"]) for i in accuracy["issues"]] == [
        (1, 200, 2.0),
        (2, 100, 0.33),
    ]
    assert accuracy["by_kind_and_capability"] == [
        {
            "kind": "implementation",
            "capability": "backlog",
            "issues": 2,
            "estimate_tokens": 400,
            "actual_tokens": 300,
            "ratio": 0.75,
        }
    ]


def test_estimate_accuracy_skips_issues_without_an_estimate() -> None:
    usage = {"owner/repo": {1: {"tests": 10}, 2: {"tests": 10}}}

    accuracy = estimate_accuracy(
        [("owner/repo", 1), ("owner/repo", 2)], [_estimate(1, None)], usage
    )

    assert accuracy == {"issues": [], "by_kind_and_capability": []}


def _accuracy(issue: int, estimate: int, actual: int) -> dict[str, Any]:
    return {
        "repo": "owner/repo",
        "number": issue,
        "kind": "implementation",
        "capability": "backlog",
        "estimate_tokens": estimate,
        "actual_tokens": actual,
        "ratio": round(actual / estimate, 2),
    }


def test_a_repeated_estimate_miss_is_a_finding_on_each_issue() -> None:
    """Scenario: Repeated estimate miss."""
    issues = [_accuracy(1, 40_000, 95_000), _accuracy(2, 10_000, 25_000)]

    findings = sprint_findings(_START, _END, issues, [])

    assert [(f["repo"], f["issue"]) for f in findings] == [("owner/repo", 1), ("owner/repo", 2)]
    assert findings[0]["text"] == (
        "Sprint 2026-09-27 to 2026-10-04 review: estimated 40000 tokens of implementation "
        "work in backlog, used 95000 (2.38x)."
    )


def test_an_estimate_within_double_is_no_finding() -> None:
    issues = [_accuracy(1, 100, 200), _accuracy(2, 100, 50)]

    assert sprint_findings(_START, _END, issues, []) == []


def test_retries_and_rework_on_an_issue_are_one_finding() -> None:
    runs = [_run(5, retries=2), _run(5, rework=1), _run(6)]

    (finding,) = sprint_findings(_START, _END, [], runs)

    assert finding["issue"] == 5
    assert finding["misses"] == [{"kind": "retry", "count": 2}, {"kind": "rework", "count": 1}]
    assert finding["text"].endswith("review: 2 retries. 1 rework.")


def _listed(
    number: int,
    state: str = "open",
    closed_at: datetime | None = None,
    state_reason: str | None = None,
    *,
    is_pull_request: bool = False,
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": state,
        "is_pull_request": is_pull_request,
        "closed_at": closed_at.isoformat().replace("+00:00", "Z") if closed_at else None,
        "state_reason": state_reason,
    }


@contextlib.contextmanager
def _ended_sprint(
    *,
    stored: dict | None = None,
    claimed: bool = True,
    unreadable: str | None = None,
):
    """A sprint with two milestones done, issues 1 and 2 delivered and 10-12 carried over."""
    inside = _START + timedelta(days=2)
    listing = [
        _listed(1, "closed", inside, "completed"),
        _listed(2, "closed", inside, "completed"),
        _listed(3, "closed", inside, "not_planned"),
        _listed(4, "closed", _START - timedelta(hours=1), "completed"),
        _listed(5, "closed", _END, "completed"),
        _listed(6, "closed", inside, is_pull_request=True),
        _listed(10),
        _listed(11),
        _listed(12),
        _listed(13),
    ]
    runs = [_run(10, retries=1), _run(11), _run(12, cost=0.25), _run(1)]

    async def list_all_issues(repo: str) -> list[dict[str, Any]]:
        if repo == unreadable:
            raise RuntimeError("gone")
        return listing

    mocks = {
        "notify": AsyncMock(return_value=True),
        "record_note": MagicMock(),
        "claim": MagicMock(
            side_effect=lambda uid, start, end, report: {"report": report} if claimed else None
        ),
    }
    with (
        patch("db.get_fuel", return_value=_fuel(resets_at=_RESETS_AT)),
        patch("tools.sprints.datetime", wraps=datetime) as clock,
        patch("db.get_sprint_report", return_value=stored),
        patch("db.list_runs", return_value=runs) as list_runs,
        patch("db.list_repos", return_value=[{"repo_name": "owner/repo"}]),
        patch("github.list_all_issues", AsyncMock(side_effect=list_all_issues)),
        patch("db.usage_by_issue", return_value={1: {"implementation": 500}, 2: {"tests": 10}}),
        patch("db.list_estimates", return_value=[_estimate(1, 100), _estimate(2, 10)]),
        patch("db.sizes_by_issue", return_value={}),
        patch(
            "db.milestones_done_between",
            return_value=[
                {"repo_name": "owner/repo", "id": 7, "title": "M7", "done_at": inside},
                {"repo_name": "owner/repo", "id": 8, "title": "M8", "done_at": inside},
            ],
        ),
        patch("db.claim_sprint_report", mocks["claim"]),
        patch("db.record_note", mocks["record_note"]),
        patch("tools.sprints.notify.send", mocks["notify"]),
    ):
        clock.now.return_value = _NOW
        mocks["list_runs"] = list_runs
        yield mocks


async def test_report_sprint_reports_the_sprint_that_ended(set_user_id: str) -> None:
    """Scenario: Sprint ends — one push and a report on delivery, spending, estimates and
    carryover."""
    with _ended_sprint() as mocks:
        result = await report_sprint()

    assert result["reported"] is True
    assert result["first_report"] is True
    assert (result["start"], result["end"]) == (_START.isoformat(), _END.isoformat())
    mocks["list_runs"].assert_called_once_with("test-uid", start=_START, end=_END)
    assert [m["id"] for m in result["delivered"]["milestones"]] == [7, 8]
    assert [i["number"] for i in result["delivered"]["issues"]] == [1, 2]
    assert [i["number"] for i in result["carryover"]] == [10, 11, 12]
    assert result["spending"]["claude"]["tokens"] == 330
    assert result["spending"]["overflow"]["cost_usd"] == 0.25
    assert [(i["number"], i["ratio"]) for i in result["estimates"]["issues"]] == [
        (1, 5.0),
        (2, 1.0),
    ]
    assert [f["issue"] for f in result["findings"]] == [1, 10]
    assert result["experiments"] == []
    assert result["unreadable"] == []

    mocks["claim"].assert_called_once()
    notes = mocks["record_note"].call_args_list
    assert [(c.args[3], c.kwargs["issue"]) for c in notes] == [("finding", 1), ("finding", 10)]
    mocks["notify"].assert_awaited_once()
    push = mocks["notify"].await_args.kwargs
    assert push["title"] == "Sprint report: 2026-09-27 to 2026-10-04"
    assert push["body"].splitlines() == [
        "Delivered: 2 milestones, 2 issues.",
        "Spent: 330 Claude tokens (unnamed: 330); 110 overflow tokens, $0.25.",
        "Estimates: actual 4.64x estimated over 2 issues.",
        "Carried over: 3 issues.",
        "Findings: 2.",
    ]


async def test_report_sprint_lists_every_open_experiment(set_user_id: str) -> None:
    with (
        _ended_sprint(),
        patch("db.list_open_experiments", return_value=[_experiment_row([50])]),
    ):
        result = await report_sprint()

    [experiment] = result["experiments"]
    assert (experiment["id"], experiment["repo"], experiment["issues"]) == (4, "owner/b", [50])
    assert (experiment["measure"], experiment["baseline"], experiment["target"]) == (
        "rework_rate",
        40.0,
        10.0,
    )
    assert (experiment["state"], experiment["results"]) == ("open", [])


async def test_report_sprint_returns_a_stored_report_without_sending(set_user_id: str) -> None:
    stored = {"report": {"start": _START.isoformat(), "findings": []}}

    with _ended_sprint(stored=stored) as mocks:
        result = await report_sprint()

    assert result == {"reported": True, "first_report": False, **stored["report"]}
    mocks["claim"].assert_not_called()
    mocks["record_note"].assert_not_called()
    mocks["notify"].assert_not_awaited()


async def test_report_sprint_that_loses_the_claim_returns_the_winners_report(
    set_user_id: str,
) -> None:
    winner = {"start": "winner", "findings": ["sentinel"]}

    with (
        _ended_sprint(claimed=False) as mocks,
        patch("db.get_sprint_report", side_effect=[None, {"report": winner}]),
    ):
        result = await report_sprint()

    assert result == {"reported": True, "first_report": False, **winner}
    mocks["record_note"].assert_not_called()
    mocks["notify"].assert_not_awaited()


async def test_report_sprint_reports_the_repos_it_can_read(set_user_id: str) -> None:
    with _ended_sprint(unreadable="owner/repo") as mocks:
        result = await report_sprint()

    assert result["unreadable"] == ["owner/repo"]
    assert result["delivered"]["issues"] == []
    mocks["notify"].assert_awaited_once()


async def test_report_sprint_with_no_fuel_reports_nothing(set_user_id: str) -> None:
    with patch("db.get_fuel", return_value=None):
        result = await report_sprint()

    assert result["reported"] is False
    assert "report_fuel" in result["explanation"]


async def test_report_sprint_is_registered_as_a_write() -> None:
    tools = {tool.name: tool for tool in await main.mcp.list_tools()}
    assert tools["report_sprint"].annotations is None or not (
        tools["report_sprint"].annotations.readOnlyHint
    )


def test_spending_splits_claude_runs_by_account() -> None:
    runs = [{**_run(1), "account": "main"}, {**_run(2), "account": "factory"}, _run(3, cost=0.1)]

    claude = sprint_spending(runs, None, _END)["claude"]

    assert claude["accounts"] == [
        {"name": "factory", "runs": 1, "tokens": 110},
        {"name": "main", "runs": 1, "tokens": 110},
    ]


def test_the_budget_counts_every_accounts_share_less_its_reserve() -> None:
    now = _END - timedelta(days=3)
    accounts = [
        {
            "name": "factory",
            "share_percent": 100.0,
            "reserve_percent": 20.0,
            "used_percent": 10.0,
            "within_pace": False,
            "usable_percent": 70.0,
            "paused": False,
        },
        {
            "name": "main",
            "share_percent": 65.0,
            "reserve_percent": 13.0,
            "used_percent": 60.0,
            "within_pace": True,
            "usable_percent": 0.0,
            "paused": False,
        },
    ]

    budget = sprint_budget(_fuel(resets_at=_END), now, 1_000, 5.0, accounts)

    # factory 80 + main 52 of 1000 tokens; main's 60 used counts only up to its 52.
    assert budget["allowance"]["budget_tokens"] == 800 + 520
    assert budget["allowance"]["spent_tokens"] == 100 + 520
    # factory is behind pace and main has nothing usable left: nothing is within pace.
    assert budget["within_pace"] is False


def test_plan_offers_splitting_an_issue_too_big_to_plan() -> None:
    """An issue sized larger than 13 is not planned itself; splitting it is."""
    entries = [_entry(1), _entry(2)]

    plan, _ = plan_sprint(entries, {}, 100, {("owner/repo", 1): db.TOO_BIG})

    assert [(item["number"], item.get("split")) for item in plan] == [(1, True), (2, None)]
