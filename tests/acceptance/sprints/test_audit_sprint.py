"""Audits in the sprint: they share its budget and appear in its review (spec change #297)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness import Lachesis, scenario

NOW = datetime.now(UTC)
REPO = "owner/reli"


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


async def _repo(lachesis: Lachesis, items: list[dict[str, Any]]) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO})
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=items)
    for item in items:
        lachesis.github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)
    lachesis.github.on("GET", f"/repos/{REPO}/pulls", json=[])


def _issue(number: int, *, state: str = "open", pull: bool = False) -> dict[str, Any]:
    stamp = (NOW - timedelta(days=1)).isoformat()
    item = {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": state,
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": stamp,
        "updated_at": stamp,
        "closed_at": stamp if state == "closed" else None,
        "state_reason": "completed" if state == "closed" else None,
    }
    if pull:
        item["pull_request"] = {"html_url": item["html_url"], "merged_at": stamp}
    return item


async def _audit_run(lachesis: Lachesis, *, finished_at: datetime, **extra: Any) -> None:
    model = (await _ok(lachesis, "route_run", {"kind": "audit"}))["model"]
    await _ok(
        lachesis,
        "report_audit_run",
        {
            "repo": REPO,
            "kind": "security",
            "outcome": "clean",
            "finished_at": finished_at.isoformat(),
            "model": model,
            "tokens_in": 15,
            "tokens_out": 15,
            **extra,
        },
    )


async def _fuel(lachesis: Lachesis, resets_at: datetime) -> None:
    await _ok(
        lachesis, "register_account", {"name": "factory", "share_percent": 100, "primary": True}
    )
    await _ok(
        lachesis,
        "report_fuel",
        {
            "account": "factory",
            "weekly_used_percent": 0,
            "weekly_resets_at": resets_at.isoformat(),
            "overflow_spent_usd": 0,
            "overflow_cap_usd": 0,
        },
    )


@pytest.mark.xfail(strict=True, reason="spec change #297")
@scenario("sprints", "Audits share the budget")
async def test_audits_share_the_budget(lachesis: Lachesis) -> None:
    await _repo(lachesis, [_issue(1), _issue(2), _issue(9, state="closed", pull=True)])
    for days_ago in (30, 20, 10, 5, 3):
        await _audit_run(lachesis, finished_at=NOW - timedelta(days=days_ago))
    await _fuel(lachesis, NOW + timedelta(days=3))

    sprint = await _ok(lachesis, "read_sprint", {})

    [audit] = [entry for entry in sprint["plan"] if entry.get("audit") == "security"]
    assert audit["estimate_tokens"] == 30
    estimates = [entry["estimate_tokens"] or 0 for entry in sprint["plan"]]
    assert sum(estimates) <= sprint["budget_tokens"]
    assert sprint["planned_tokens"] == sum(estimates)


@pytest.mark.xfail(strict=True, reason="spec change #297")
@scenario("sprints", "Audits in the review")
async def test_audits_in_the_review(lachesis: Lachesis) -> None:
    await _repo(lachesis, [_issue(40), _issue(41)])
    await _audit_run(
        lachesis,
        finished_at=NOW - timedelta(days=2),
        outcome="findings",
        issues=[40, 41],
    )
    # The week that just ended holds the audit: it reset a moment ago.
    await _fuel(lachesis, datetime.now(UTC) + timedelta(days=7) - timedelta(seconds=1))
    await _fuel(lachesis, datetime.now(UTC) - timedelta(seconds=1))

    review = await _ok(lachesis, "report_sprint", {})

    [audit] = [
        entry
        for entry in review.get("audits", [])
        if entry["repo"] == REPO and entry["kind"] == "security"
    ]
    assert sorted(audit["issues"]) == [40, 41]
