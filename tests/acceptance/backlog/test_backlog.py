"""The backlog: one computed order per repository, and the next issue drawn from it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
NOW = datetime.now(UTC)


def _issue(
    number: int,
    *,
    age: timedelta = timedelta(hours=1),
    labels: tuple[str, ...] = (),
    body: str = "",
) -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": body,
        "state": "open",
        "labels": [{"name": label} for label in labels],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": (NOW - age).isoformat(),
        "updated_at": (NOW - age).isoformat(),
        "closed_at": None,
        "state_reason": None,
    }


def _spec_change(number: int) -> dict[str, Any]:
    return {
        **_issue(number, age=timedelta(days=30), labels=("spec-change",)),
        "pull_request": {"html_url": f"https://github.com/{REPO}/pull/{number}"},
    }


def _implementing(spec_change: int) -> str:
    return f"Build it.\n\n---\nSpec change: #{spec_change}\nCapability: widgets"


async def _repo_with(lachesis: Lachesis, *items: dict[str, Any]) -> None:
    await lachesis.call_tool("register_repo", {"repo": REPO})
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=list(items))
    for item in items:
        lachesis.github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)


async def _active_milestone(lachesis: Lachesis, title: str, spec_change: int) -> int:
    created = await lachesis.call_tool(
        "create_milestone",
        {"repo": REPO, "title": title, "goal": f"{title} ships", "spec_changes": [spec_change]},
    )
    assert not created.is_error, created.text
    milestone = created.json()["id"]
    started = await lachesis.call_tool("start_milestone", {"repo": REPO, "milestone": milestone})
    assert not started.is_error, started.text
    return milestone


def _uses_specs(lachesis: Lachesis) -> None:
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )


def _blocked_by(lachesis: Lachesis, number: int, blocker: dict[str, Any]) -> None:
    lachesis.github.on(
        "GET", f"/repos/{REPO}/issues/{number}/dependencies/blocked_by", json=[blocker]
    )


async def _report_fuel(
    lachesis: Lachesis, *, weekly_used_percent: float, overflow_spent_usd: float
) -> None:
    result = await lachesis.call_tool(
        "report_fuel",
        {
            "weekly_used_percent": weekly_used_percent,
            "weekly_resets_at": (NOW + timedelta(days=3)).isoformat(),
            "overflow_spent_usd": overflow_spent_usd,
            "overflow_cap_usd": 50,
        },
    )
    assert not result.is_error, result.text


async def _backlog(lachesis: Lachesis) -> list[dict[str, Any]]:
    result = await lachesis.call_tool("get_backlog", {"repo": REPO})
    assert not result.is_error, result.text
    return result.json()


async def _next(lachesis: Lachesis) -> dict[str, Any]:
    result = await lachesis.call_tool("next_issue", {"repo": REPO})
    assert not result.is_error, result.text
    return result.json()


async def _set_flags(lachesis: Lachesis, number: int, **flags: bool) -> None:
    result = await lachesis.call_tool(
        "set_backlog_flags", {"repo": REPO, "issue_number": number, **flags}
    )
    assert not result.is_error, result.text


@scenario("backlog", "Reading the backlog")
async def test_reading_the_backlog(lachesis: Lachesis) -> None:
    await _repo_with(
        lachesis,
        _issue(1, age=timedelta(days=4)),
        _issue(2, age=timedelta(days=3)),
        _issue(3, age=timedelta(days=2)),
        _issue(4, age=timedelta(days=1)),
    )
    await _set_flags(lachesis, 3, interrupt=True)

    backlog = await _backlog(lachesis)

    assert backlog[0]["number"] == 3
    assert sorted(entry["number"] for entry in backlog) == [1, 2, 3, 4]
    for entry in backlog:
        assert {"position", "tier", "ready", "interrupt"} <= entry.keys()
    assert [entry["position"] for entry in backlog] == [0, 1, 2, 3]
    assert [entry["interrupt"] for entry in backlog] == [True, False, False, False]
    assert all(entry["ready"] for entry in backlog)


@scenario("backlog", "Security before milestone work")
async def test_security_before_milestone_work(lachesis: Lachesis) -> None:
    _uses_specs(lachesis)
    await _repo_with(
        lachesis,
        _spec_change(5),
        _issue(10, age=timedelta(days=1), body=_implementing(5)),
        _issue(
            11,
            age=timedelta(minutes=5),
            labels=("security", "spec-gap"),
            body="Close the hole.\n\n---\nSpec gap: yes\nCapability: widgets",
        ),
    )
    await _active_milestone(lachesis, "Widgets", 5)

    backlog = await _backlog(lachesis)

    assert [entry["number"] for entry in backlog] == [11, 10]


@scenario("backlog", "Starving work rises")
async def test_starving_work_rises(lachesis: Lachesis) -> None:
    await _report_fuel(lachesis, weekly_used_percent=10, overflow_spent_usd=0)
    # The sprint started 4 days ago, so two whole sprints before it is 18 days ago.
    await _repo_with(lachesis, _issue(1, age=timedelta(days=19)), _issue(2, age=timedelta(days=17)))

    backlog = {entry["number"]: entry for entry in await _backlog(lachesis)}

    assert backlog[2]["tier"] == "other"
    assert backlog[1]["tier"] == "milestone"


@scenario("backlog", "Focusing a milestone")
async def test_focusing_a_milestone(lachesis: Lachesis) -> None:
    _uses_specs(lachesis)
    await _repo_with(
        lachesis,
        _spec_change(5),
        _spec_change(6),
        _issue(10, age=timedelta(days=2), body=_implementing(5)),
        _issue(11, age=timedelta(days=1), body=_implementing(6)),
    )
    await _active_milestone(lachesis, "A", 5)
    b = await _active_milestone(lachesis, "B", 6)

    focused = await lachesis.call_tool("set_focus", {"repo": REPO, "milestone": b})
    backlog = await _backlog(lachesis)

    assert not focused.is_error, focused.text
    assert [entry["number"] for entry in backlog] == [11, 10]


@scenario("backlog", "Park and unpark")
async def test_park_and_unpark(lachesis: Lachesis) -> None:
    await _repo_with(lachesis, _issue(1, age=timedelta(days=2)), _issue(2, age=timedelta(days=1)))

    await _set_flags(lachesis, 1, ready=False)
    while_parked = await _next(lachesis)
    await _set_flags(lachesis, 1, ready=True)
    unparked = await _next(lachesis)
    backlog = await _backlog(lachesis)

    assert while_parked["issue"]["number"] == 2
    assert unparked["issue"]["number"] == 1
    assert (backlog[0]["number"], backlog[0]["ready"]) == (1, True)


@scenario("backlog", "Interrupt wins over every tier")
async def test_interrupt_wins_over_every_tier(lachesis: Lachesis) -> None:
    await _repo_with(
        lachesis,
        _issue(10, age=timedelta(days=3)),
        _issue(20, age=timedelta(days=2), labels=("security",)),
        _issue(30, age=timedelta(days=1)),
    )

    await _set_flags(lachesis, 30, interrupt=True)
    offered = await _next(lachesis)

    assert offered["issue"]["number"] == 30


@scenario("backlog", "Everything blocked")
async def test_everything_blocked(lachesis: Lachesis) -> None:
    blocker = _issue(3)
    await _repo_with(
        lachesis, _issue(1, age=timedelta(days=2)), _issue(2, age=timedelta(days=1)), blocker
    )
    await _set_flags(lachesis, 3, ready=False)
    _blocked_by(lachesis, 1, blocker)
    _blocked_by(lachesis, 2, blocker)

    result = await _next(lachesis)

    assert result["issue"] is None
    assert result["reason"] == "all_blocked"


@scenario("backlog", "Skip work already in progress")
@pytest.mark.xfail(strict=True, reason="spec gap")
async def test_skip_work_already_in_progress(lachesis: Lachesis) -> None:
    pull = {
        **_issue(7, body="Fixes #1"),
        "pull_request": {"html_url": f"https://github.com/{REPO}/pull/7"},
    }
    await _repo_with(lachesis, _issue(1, age=timedelta(days=2)), _issue(2), pull)
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/pulls",
        json=[
            {
                "number": 7,
                "title": pull["title"],
                "body": "Fixes #1",
                "state": "open",
                "html_url": pull["pull_request"]["html_url"],
                "head": {"ref": "fix-1"},
                "labels": [],
            }
        ],
    )

    result = await _next(lachesis)

    assert result["issue"]["number"] == 2


@scenario("backlog", "Waiting on the author")
async def test_waiting_on_author(lachesis: Lachesis) -> None:
    first, second = _issue(1, age=timedelta(days=2)), _issue(2, age=timedelta(days=1))
    await _repo_with(lachesis, first, second)
    lachesis.github.on(
        "POST",
        f"/repos/{REPO}/issues/1/comments",
        json={"html_url": f"https://github.com/{REPO}/issues/1#issuecomment-1"},
        status=201,
    )
    lachesis.github.on("POST", f"/repos/{REPO}/labels", json={"name": "needs-author"}, status=201)
    lachesis.github.on("POST", f"/repos/{REPO}/issues/1/labels", json=[])
    lachesis.github.on("DELETE", f"/repos/{REPO}/issues/1/labels/needs-author", json=[])

    asked = await lachesis.call_tool(
        "ask_author", {"repo": REPO, "number": 1, "question": "Which colour?"}
    )
    assert not asked.is_error, asked.text
    await _repo_with(lachesis, _issue(1, age=timedelta(days=2), labels=("needs-author",)), second)
    while_waiting = await _next(lachesis)
    answered = await lachesis.call_tool(
        "answer_author", {"repo": REPO, "number": 1, "answer": "Blue."}
    )
    assert not answered.is_error, answered.text
    assert lachesis.github.requests_to("DELETE", f"/repos/{REPO}/issues/1/labels/needs-author")
    await _repo_with(lachesis, first, second)
    once_answered = await _next(lachesis)

    assert while_waiting["issue"]["number"] == 2
    assert once_answered["issue"]["number"] == 1


@scenario("backlog", "Budget spent")
async def test_budget_spent(lachesis: Lachesis) -> None:
    await _report_fuel(lachesis, weekly_used_percent=100, overflow_spent_usd=50)
    await _repo_with(lachesis, _issue(1, age=timedelta(days=2)), _issue(2, age=timedelta(days=1)))

    result = await _next(lachesis)

    assert result["issue"] is None
    assert result["reason"] == "budget_spent"
