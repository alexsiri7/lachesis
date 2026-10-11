"""The factory improves itself every sprint, and stops the line for a repeated failure
(spec change #327).

The contract these tests set for the implementation issues:

- ``record_experiment(repo, title, change, measure, baseline, target, issues)`` records an
  improvement experiment: the change, the measure it should move (such as
  ``rework_rate``), its value now, the target, and the issues that hold its work. It returns
  the experiment with its ``id``, those fields, ``state`` ``open`` and ``results`` (empty).
- ``judge_experiment(id, value, verdict, reason)`` judges an open experiment against its
  measure at a review: *verdict* is ``kept``, ``reverted`` or ``extended`` (one more
  sprint, at most once). It returns the experiment with its ``state`` (``kept``,
  ``reverted`` or ``open`` while extended) and the result appended to ``results``.
- ``report_sprint`` adds ``experiments``, every experiment open at the review with its
  measure, baseline, target and results, and ``stop_the_line``, each failure repeated three
  times in the sprint: ``{"outcome", "capability", "issues"}``. Fixing each repeat's cause
  becomes an experiment whatever else is chosen: the review opens it with the repeat's
  issues, and it is listed in ``experiments``.
- ``read_sprint`` adds ``improvement_budget_tokens``, the improvement share (10 percent by
  default) of ``budget_tokens``; the open experiments' issues are planned ahead of the
  ordinary backlog, marked ``improvement: true``, their estimates within that share.

A sprint is ended on demand: reporting the next weekly reset as just under seven days away
starts a new sprint a second ago.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
WIDGETS = "Build it.\n\n---\nSpec change: #1\nCapability: widgets"


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _now() -> datetime:
    return datetime.now(UTC)


def _issue(number: int, *, body: str = "", closed_at: datetime | None = None) -> dict[str, Any]:
    stamp = (_now() - timedelta(days=1)).isoformat()
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": body,
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

    def open(self, *numbers: int, body: str = "") -> None:
        for number in numbers:
            self.items[number] = _issue(number, body=body)
        self._publish()

    def close(self, *numbers: int) -> None:
        for number in numbers:
            self.items[number] = _issue(number, body=self.items[number]["body"], closed_at=_now())
        self._publish()

    async def size(self, number: int, size: int) -> None:
        await _ok(self.lachesis, "size_issue", {"repo": REPO, "issue": number, "size": size})

    async def estimate(self, number: int) -> int:
        estimate = await _ok(
            self.lachesis,
            "estimate_issue",
            {"repo": REPO, "issue": number, "kind": "implementation"},
        )
        return estimate["estimate_tokens"]

    async def run(self, number: int, tokens: int = 100, *, outcome: str | None = None) -> None:
        await _ok(
            self.lachesis,
            "report_usage",
            {
                "repo": REPO,
                "issue": number,
                "model": "claude",
                "kind": "implementation",
                "tokens_in": tokens // 2,
                "tokens_out": tokens - tokens // 2,
                "account": "factory",
            },
        )
        if outcome is not None:
            await _ok(
                self.lachesis,
                "report_run_outcome",
                {"repo": REPO, "issue": number, "kind": "implementation", "outcome": outcome},
            )


async def _new_sprint(lachesis: Lachesis, *, first: bool = False) -> None:
    """Start a sprint a second ago: the factory account's weekly allowance has just reset.

    Waiting out that second first keeps what the tests did before in the sprint that ends.
    """
    if first:
        await _ok(
            lachesis,
            "register_account",
            {"name": "factory", "share_percent": 100, "primary": True},
        )
    else:
        await asyncio.sleep(1.1)
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


async def _experiment(lachesis: Lachesis, issues: list[int], **fields: Any) -> dict[str, Any]:
    return await _ok(
        lachesis,
        "record_experiment",
        {
            "repo": REPO,
            "title": "Review the diff before asking for review",
            "change": "The implementation guidance asks for a self-review of the diff.",
            "measure": "rework_rate",
            "baseline": 40,
            "target": 10,
            "issues": issues,
            **fields,
        },
    )


def _listed(review: dict[str, Any], experiment: dict[str, Any]) -> dict[str, Any]:
    [listed] = [entry for entry in review["experiments"] if entry["id"] == experiment["id"]]
    return listed


@scenario("sprints", "Experiment chosen")
async def test_experiment_chosen(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _new_sprint(lachesis, first=True)
    repo.open(1, 2, 3, 4, 60)
    for number in (1, 2, 3, 4):
        await repo.run(number, outcome="rework")
    repo.close(1, 2, 3, 4)
    await _new_sprint(lachesis)
    review = await _ok(lachesis, "report_sprint", {})
    reworked = [
        f["issue"] for f in review["findings"] if {"kind": "rework", "count": 1} in f["misses"]
    ]
    assert sorted(reworked) == [1, 2, 3, 4]
    repo.open(50)

    experiment = await _experiment(lachesis, [50], baseline=100, target=25)
    sprint = await _ok(lachesis, "read_sprint", {})

    assert experiment["state"] == "open"
    assert experiment["change"]
    assert (experiment["measure"], experiment["baseline"], experiment["target"]) == (
        "rework_rate",
        100,
        25,
    )
    first, *rest = sprint["plan"]
    assert (first["number"], first.get("improvement")) == (50, True)
    assert [(entry["number"], entry.get("improvement", False)) for entry in rest] == [(60, False)]


@scenario("sprints", "Experiment kept")
async def test_experiment_kept(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _new_sprint(lachesis, first=True)
    repo.open(50)
    experiment = await _experiment(lachesis, [50], baseline=40, target=10)
    await _new_sprint(lachesis)

    review = await _ok(lachesis, "report_sprint", {})
    judged = await _ok(
        lachesis,
        "judge_experiment",
        {
            "id": experiment["id"],
            "value": 6,
            "verdict": "kept",
            "reason": "Rework fell to 6 percent; the self-review is now standard guidance.",
        },
    )

    assert _listed(review, experiment)["measure"] == "rework_rate"
    assert judged["state"] == "kept"
    assert judged["results"][-1]["value"] == 6


@scenario("sprints", "Experiment reverted")
async def test_experiment_reverted(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _new_sprint(lachesis, first=True)
    repo.open(50)
    experiment = await _experiment(lachesis, [50], baseline=40, target=10)
    await _new_sprint(lachesis)
    await _ok(lachesis, "report_sprint", {})
    await _ok(
        lachesis,
        "judge_experiment",
        {
            "id": experiment["id"],
            "value": 30,
            "verdict": "extended",
            "reason": "Half the sprint's issues predate the change.",
        },
    )
    await _new_sprint(lachesis)

    review = await _ok(lachesis, "report_sprint", {})
    extended_again = await lachesis.call_tool(
        "judge_experiment",
        {"id": experiment["id"], "value": 35, "verdict": "extended", "reason": "Once more."},
    )
    judged = await _ok(
        lachesis,
        "judge_experiment",
        {
            "id": experiment["id"],
            "value": 35,
            "verdict": "reverted",
            "reason": "Rework did not fall in two sprints; the guidance change is undone.",
        },
    )

    assert [result["value"] for result in _listed(review, experiment)["results"]] == [30]
    assert extended_again.is_error
    assert judged["state"] == "reverted"


@scenario("sprints", "Improvement share respected")
async def test_improvement_share_respected(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _new_sprint(lachesis, first=True)
    budget = (await _ok(lachesis, "read_sprint", {}))["budget_tokens"]
    history = range(10, 15)
    repo.open(*history)
    for number in history:
        await repo.size(number, 1)
        await repo.run(number, budget // 50)  # a point costs 2 percent of the budget
    repo.close(*history)
    await _new_sprint(lachesis)
    repo.open(51, 52, 60)
    for number, size in ((51, 3), (52, 3), (60, 1)):  # 6 + 6 percent of improvement work
        await repo.size(number, size)
        await repo.estimate(number)
    await _experiment(lachesis, [51, 52])

    sprint = await _ok(lachesis, "read_sprint", {})

    share = sprint["improvement_budget_tokens"]
    assert sprint["budget_tokens"] * 0.10 - 1 <= share <= sprint["budget_tokens"] * 0.10
    improvement = [entry for entry in sprint["plan"] if entry.get("improvement")]
    assert [entry["number"] for entry in improvement] == [51]
    assert sum(entry["estimate_tokens"] for entry in improvement) <= share
    assert sprint["plan"][: len(improvement)] == improvement
    assert 52 not in [entry["number"] for entry in sprint["plan"]]
    assert 60 in [entry["number"] for entry in sprint["plan"]]


@scenario("sprints", "Same failure three times")
async def test_same_failure_three_times(lachesis: Lachesis) -> None:
    repo = await Repo(lachesis).register()
    await _new_sprint(lachesis, first=True)
    repo.open(1, 2, 3, body=WIDGETS)
    for number in (1, 2, 3):  # each pull request stuck on the same conflict, its run retried
        await repo.run(number, outcome="retry")
    await _new_sprint(lachesis)

    review = await _ok(lachesis, "report_sprint", {})

    [repeat] = review["stop_the_line"]
    assert (repeat["outcome"], repeat["capability"]) == ("retry", "widgets")
    assert sorted(repeat["issues"]) == [1, 2, 3]
    [fix] = [entry for entry in review["experiments"] if set(entry["issues"]) >= {1, 2, 3}]
    assert fix["state"] == "open"
