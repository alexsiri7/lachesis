"""Sizes, priced points, capacity and the sprint review (spec change #303).

The contract these tests set for the implementation issues:

- ``size_issue(repo, issue, size)`` sets an issue's size: 1, 2, 3, 5, 8 or 13, or 21 for
  "larger than 13"; ``create_github_issues`` also takes ``size`` per issue.
- ``estimate_issue(repo, issue, kind, model=None)`` returns ``size``, ``estimate_tokens``,
  ``confidence`` and ``tokens_per_point``.
- ``read_sprint`` adds ``capacity_points``, ``planned_points`` and ``velocity`` (points
  delivered, sprint by sprint); a plan entry for an issue too big to plan is
  ``{"repo", "number", "split": true}``.
- ``report_sprint`` adds ``points`` (``planned``, ``delivered``), ``sizes_misjudged`` and
  per-model spending under ``spending.models``.

A sprint is ended on demand: reporting the next weekly reset as just under seven days away
starts a new sprint a second ago.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
MODEL = "claude"


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _now() -> datetime:
    return datetime.now(UTC)


def _issue(number: int, *, closed_at: datetime | None = None) -> dict[str, Any]:
    stamp = (_now() - timedelta(days=1)).isoformat()
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "closed" if closed_at else "open",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": stamp,
        "updated_at": stamp,
        "closed_at": closed_at.isoformat() if closed_at else None,
        "state_reason": "completed" if closed_at else None,
    }


class Repo:
    """The registered repository and its issue listing, which tests grow as they go."""

    def __init__(self, lachesis: Lachesis) -> None:
        self.lachesis = lachesis
        self.items: dict[int, dict[str, Any]] = {}

    async def register(self) -> Repo:
        await _ok(self.lachesis, "register_repo", {"repo": REPO})
        self.lachesis.github.on("GET", f"/repos/{REPO}/pulls", json=[])
        self._publish()
        return self

    def _publish(self) -> None:
        self.lachesis.github.on("GET", f"/repos/{REPO}/issues", json=list(self.items.values()))
        for number, item in self.items.items():
            self.lachesis.github.on("GET", f"/repos/{REPO}/issues/{number}", json=item)

    def open(self, *numbers: int) -> None:
        for number in numbers:
            self.items[number] = _issue(number)
        self._publish()

    def close(self, *numbers: int, at: datetime | None = None) -> None:
        for number in numbers:
            self.items[number] = _issue(number, closed_at=at or _now())
        self._publish()

    async def size(self, number: int, size: int) -> None:
        await _ok(self.lachesis, "size_issue", {"repo": REPO, "issue": number, "size": size})

    async def run(
        self, number: int, tokens: int, *, kind: str = "implementation", model: str = MODEL
    ) -> None:
        await _ok(
            self.lachesis,
            "report_usage",
            {
                "repo": REPO,
                "issue": number,
                "model": model,
                "kind": kind,
                "tokens_in": tokens // 2,
                "tokens_out": tokens - tokens // 2,
                "account": "factory",
            },
        )


async def _new_sprint(lachesis: Lachesis, *, first: bool = False) -> None:
    """Start a sprint now: the factory account's weekly allowance has just reset."""
    if first:
        await _ok(
            lachesis,
            "register_account",
            {"name": "factory", "share_percent": 100, "primary": True},
        )
    await asyncio.sleep(0.05)
    await _ok(
        lachesis,
        "report_fuel",
        {
            "account": "factory",
            "weekly_used_percent": 0,
            "weekly_resets_at": (_now() + timedelta(days=7, seconds=-1)).isoformat(),
            "overflow_spent_usd": 0,
            "overflow_cap_usd": 0,
        },
    )
    await asyncio.sleep(0.05)


async def _history(repo: Repo, *, issues: range, size: int, tokens: int) -> None:
    """Closed implementation issues of one size, each costing *tokens*."""
    repo.open(*issues)
    for number in issues:
        await repo.size(number, size)
        await repo.run(number, tokens)
    repo.close(*issues)


@scenario("sprints", "Issue sized on arrival")
async def test_issue_sized_on_arrival(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    lachesis.github.on("POST", f"/repos/{REPO}/issues", json=_issue(7), status=201)
    repo.open(7)

    created = await lachesis.call_tool(
        "create_github_issues",
        {"repo": REPO, "issues": [{"title": "Keep widgets", "body": "Build it.", "size": 5}]},
    )
    refused = await lachesis.call_tool("size_issue", {"repo": REPO, "issue": 7, "size": 4})
    estimate = await _ok(
        lachesis, "estimate_issue", {"repo": REPO, "issue": 7, "kind": "implementation"}
    )

    assert not created.is_error, created.text
    assert refused.is_error
    assert estimate["size"] == 5


@scenario("sprints", "Too big to plan")
async def test_too_big_to_plan(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    repo.open(8)
    await repo.size(8, 21)
    await _new_sprint(lachesis, first=True)

    sprint = await _ok(lachesis, "read_sprint", {})

    entries = [entry for entry in sprint["plan"] if entry["number"] == 8]
    assert entries == [{**entries[0], "split": True}]


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("sprints", "Estimate from points")
async def test_estimate_from_points(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _history(repo, issues=range(10, 18), size=5, tokens=10)  # 2 per point, eight closed
    await _new_sprint(lachesis, first=True)
    repo.open(30)
    await repo.size(30, 5)

    estimate = await _ok(
        lachesis,
        "estimate_issue",
        {"repo": REPO, "issue": 30, "kind": "implementation", "model": MODEL},
    )

    assert estimate["tokens_per_point"] == 2
    assert estimate["estimate_tokens"] == 10
    assert estimate["confidence"] == "normal"


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("sprints", "How far the budget goes")
async def test_how_far_the_budget_goes(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _new_sprint(lachesis, first=True)
    budget = (await _ok(lachesis, "read_sprint", {}))["budget_tokens"]
    await _history(repo, issues=range(10, 15), size=1, tokens=budget // 25)  # 25 points' worth
    await _new_sprint(lachesis)

    sprint = await _ok(lachesis, "read_sprint", {})

    assert sprint["capacity_points"] == 25


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("sprints", "Sprint ends")
async def test_sprint_ends(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _new_sprint(lachesis, first=True)
    repo.open(1, 2, 3, 4, 5)
    for number in (1, 2, 3, 4, 5):
        await repo.size(number, 2)
        await repo.run(number, 100, model=MODEL if number % 2 else "claude-sonnet")
    repo.close(1, 2)
    await _new_sprint(lachesis)

    review = await _ok(lachesis, "report_sprint", {})

    delivered = [entry["number"] for entry in review["delivered"]["issues"]]
    assert sorted(delivered) == [1, 2]
    assert sorted(entry["number"] for entry in review["carryover"]) == [3, 4, 5]
    models = review["spending"]["models"]
    assert {entry["model"] for entry in models} == {MODEL, "claude-sonnet"}
    assert "overflow" in review["spending"]


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("sprints", "Points planned against delivered")
async def test_points_planned_against_delivered(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    sizes = {1: 13, 2: 8, 3: 5, 4: 5, 5: 3, 6: 3, 7: 2, 8: 1}  # 40 points
    repo.open(*sizes)
    for number, size in sizes.items():
        await repo.size(number, size)
    await _new_sprint(lachesis, first=True)
    planned = await _ok(lachesis, "read_sprint", {})
    assert planned["planned_points"] == 40
    for number in (1, 2, 3, 5, 7):  # 13 + 8 + 5 + 3 + 2 = 31
        await repo.run(number, 10)
    repo.close(1, 2, 3, 5, 7)
    await _new_sprint(lachesis)

    review = await _ok(lachesis, "report_sprint", {})
    sprint = await _ok(lachesis, "read_sprint", {})

    assert review["points"] == {"planned": 40, "delivered": 31}
    assert sprint["velocity"][-1] == 31


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("sprints", "Size misjudged")
async def test_size_misjudged(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _history(repo, issues=range(10, 15), size=1, tokens=4)  # 4 per point
    await _new_sprint(lachesis, first=True)
    repo.open(40)
    await repo.size(40, 2)
    estimate = await _ok(
        lachesis, "estimate_issue", {"repo": REPO, "issue": 40, "kind": "implementation"}
    )
    assert estimate["estimate_tokens"] == 8
    await repo.run(40, 20)
    repo.close(40)
    await _new_sprint(lachesis)

    review = await _ok(lachesis, "report_sprint", {})

    assert [entry["number"] for entry in review["sizes_misjudged"]] == [40]


@pytest.mark.xfail(strict=True, reason="spec change #303")
@scenario("sprints", "Repeated estimate miss")
async def test_repeated_estimate_miss(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _history(repo, issues=range(10, 15), size=1, tokens=4)
    await _new_sprint(lachesis, first=True)
    repo.open(40, 41, 42)
    for number in (40, 41, 42):
        await repo.size(number, 2)
        await _ok(
            lachesis, "estimate_issue", {"repo": REPO, "issue": number, "kind": "implementation"}
        )
        await repo.run(number, 20)
    repo.close(40, 41, 42)
    lachesis.github.on("POST", f"/repos/{REPO}/issues/40/comments", json={"id": 1}, status=201)
    await _new_sprint(lachesis)

    review = await _ok(lachesis, "report_sprint", {})
    notes = await _ok(lachesis, "list_notes", {"repo": REPO, "issue": 40})

    assert review["findings"]
    assert notes
