"""Sprint planning: the backlog in order, as far as its estimates fill the budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, scenario

NOW = datetime.now(UTC)
REPO = "owner/repo"
OTHER = "owner/other"


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _issue(repo: str, number: int, *, closed: bool = False) -> dict[str, Any]:
    stamp = (NOW - timedelta(days=1)).isoformat()
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "closed" if closed else "open",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "created_at": stamp,
        "updated_at": stamp,
        "closed_at": stamp if closed else None,
        "state_reason": "completed" if closed else None,
    }


async def _repo(lachesis: Lachesis, repo: str, items: list[dict[str, Any]]) -> None:
    await _ok(lachesis, "register_repo", {"repo": repo})
    lachesis.github.on("GET", f"/repos/{repo}/issues", json=items)
    for item in items:
        lachesis.github.on("GET", f"/repos/{repo}/issues/{item['number']}", json=item)
    lachesis.github.on("GET", f"/repos/{repo}/pulls", json=[])


async def _sprint(lachesis: Lachesis) -> None:
    await _ok(
        lachesis, "register_account", {"name": "factory", "share_percent": 100, "primary": True}
    )
    await _ok(
        lachesis,
        "report_fuel",
        {
            "account": "factory",
            "weekly_used_percent": 0,
            "weekly_resets_at": (NOW + timedelta(days=3)).isoformat(),
            "overflow_spent_usd": 0,
            "overflow_cap_usd": 0,
        },
    )


@scenario("sprints", "Plan fills the budget")
async def test_plan_fills_the_budget(lachesis: Lachesis) -> None:
    # A point of implementation costs 40 units and a point of tests 30, each from five
    # closed one-point issues.
    implemented, tested = range(10, 15), range(20, 25)
    await _repo(
        lachesis,
        REPO,
        [_issue(REPO, n, closed=True) for n in (*implemented, *tested)]
        + [_issue(REPO, n) for n in (1, 2, 3)],
    )
    await _sprint(lachesis)
    unit = (await _ok(lachesis, "read_sprint", {}))["budget_tokens"] / 100
    for numbers, kind, units in ((implemented, "implementation", 40), (tested, "tests", 30)):
        for number in numbers:
            await _ok(lachesis, "size_issue", {"repo": REPO, "issue": number, "size": 1})
            tokens = round(units * unit)
            await _ok(
                lachesis,
                "report_usage",
                {
                    "repo": REPO,
                    "issue": number,
                    "model": "claude",
                    "kind": kind,
                    "tokens_in": tokens // 2,
                    "tokens_out": tokens - tokens // 2,
                    "account": "factory",
                },
            )
    for number, kind in ((1, "implementation"), (2, "implementation"), (3, "tests")):
        await _ok(lachesis, "size_issue", {"repo": REPO, "issue": number, "size": 1})
        await _ok(lachesis, "estimate_issue", {"repo": REPO, "issue": number, "kind": kind})

    sprint = await _ok(lachesis, "read_sprint", {})

    planned = [(entry["number"], entry["estimate_tokens"]) for entry in sprint["plan"]]
    assert planned == [(1, round(40 * unit)), (2, round(40 * unit))]
    assert sprint["not_planned"] == 1


@scenario("sprints", "Focus changes mid-sprint")
async def test_focus_changes_mid_sprint(lachesis: Lachesis) -> None:
    await _repo(lachesis, REPO, [_issue(REPO, 1)])
    await _repo(lachesis, OTHER, [_issue(OTHER, 2)])
    await _sprint(lachesis)
    before = await _ok(lachesis, "read_sprint", {})

    await _ok(lachesis, "set_focus", {"repo": OTHER})
    after = await _ok(lachesis, "read_sprint", {})

    def order(sprint: dict[str, Any]) -> list[tuple[str, int]]:
        return [(entry["repo"], entry["number"]) for entry in sprint["plan"]]

    assert order(before) == [(REPO, 1), (OTHER, 2)]
    assert order(after) == [(OTHER, 2), (REPO, 1)]
