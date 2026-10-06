"""The web view: fuel and the sprint, the backlog and what is next, and milestones.

The page marks what it shows with data- attributes, which these tests read, so that
wording and layout can change without changing what is checked.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
NOW = datetime.now(UTC)


class _Elements(HTMLParser):
    """Every element's attributes, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append({"tag": tag, **{k: v or "" for k, v in attrs}})


def _elements(html: str) -> list[dict[str, str]]:
    parser = _Elements()
    parser.feed(html)
    return parser.elements


def _section(html: str, section_id: str) -> str:
    match = re.search(rf'<section[^>]*\bid="{section_id}"[^>]*>(.*?)</section>', html, re.DOTALL)
    assert match is not None, f"no section {section_id!r} on the page"
    return match.group(1)


def _with(elements: list[dict[str, str]], attribute: str) -> list[dict[str, str]]:
    return [element for element in elements if attribute in element]


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _issue(
    number: int,
    *,
    state: str = "open",
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
        "state": state,
        "labels": [{"name": label} for label in labels],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": (NOW - age).isoformat(),
        "updated_at": (NOW - age).isoformat(),
        "closed_at": (NOW - timedelta(minutes=5)).isoformat() if state == "closed" else None,
        "state_reason": "completed" if state == "closed" else None,
    }


@scenario("progress-reporting", "Two accounts on the page")
async def test_two_accounts_on_the_page(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_account", {"name": "factory", "share_percent": 100})
    await _ok(
        lachesis,
        "register_account",
        {"name": "main", "share_percent": 65, "five_hour_pause_percent": 60},
    )
    resets = (NOW + timedelta(days=3)).isoformat()
    await _ok(
        lachesis,
        "report_fuel",
        {"account": "factory", "weekly_used_percent": 100, "weekly_resets_at": resets},
    )
    await _ok(
        lachesis,
        "report_fuel",
        {"account": "main", "weekly_used_percent": 19, "weekly_resets_at": resets},
    )
    await _ok(lachesis, "report_overflow", {"spent_usd": 0, "cap_usd": 10})

    fuel = _elements(_section(await lachesis.web_page(), "fuel"))

    accounts = {e["data-account"]: e for e in _with(fuel, "data-account")}
    assert float(accounts["factory"]["data-used"]) == 100
    assert float(accounts["main"]["data-used"]) == 19
    assert float(accounts["main"]["data-reserve"]) == 13
    assert float(accounts["main"]["data-usable"]) == 33
    [overflow] = _with(fuel, "data-overflow-cap")
    assert float(overflow["data-overflow-spent"]) == 0
    assert float(overflow["data-overflow-cap"]) == 10
    [sprint] = _with(fuel, "data-sprint-budget")
    assert int(sprint["data-sprint-budget"]) > 0
    assert "data-sprint-spent" in sprint
    assert "data-sprint-left" in sprint
    assert datetime.fromisoformat(sprint["data-sprint-end"]) == datetime.fromisoformat(resets)


@scenario("progress-reporting", "No fuel reported")
async def test_no_fuel_reported(lachesis: Lachesis) -> None:
    fuel = _section(await lachesis.web_page(), "fuel")

    assert "No fuel has been reported" in fuel


@scenario("progress-reporting", "Next up")
async def test_next_up(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO})
    issues = [_issue(3, age=timedelta(days=2)), _issue(7, age=timedelta(hours=1))]
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=issues)
    for item in issues:
        lachesis.github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)
    await _ok(lachesis, "set_backlog_flags", {"repo": REPO, "issue_number": 7, "interrupt": True})

    backlog = _elements(_section(await lachesis.web_page(), "backlog"))

    listed = [e for e in _with(backlog, "data-issue") if e.get("data-repo") == REPO]
    order = [int(e["data-issue"]) for e in listed]
    assert order.index(7) < order.index(3)
    assert next(e for e in listed if e["data-issue"] == "7")["data-tier"] == "interrupt"
    [next_up] = _with(backlog, "data-next-issue")
    assert next_up["data-next-issue"] == "7"
    assert next_up["data-next-repo"] == REPO


@scenario("progress-reporting", "Active milestone")
async def test_active_milestone(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO})
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    reference = "Build it.\n\n---\nSpec change: #5\nCapability: widgets"
    spec_change = {
        **_issue(5, state="closed", age=timedelta(days=30), labels=("spec-change",)),
        "pull_request": {
            "html_url": f"https://github.com/{REPO}/pull/5",
            "merged_at": (NOW - timedelta(days=29)).isoformat(),
        },
    }
    done = _issue(10, state="closed", body=reference)
    open_ = _issue(11, body=reference)
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=[spec_change, done, open_])
    for item in (spec_change, done, open_):
        lachesis.github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)
    lachesis.github.on(
        "POST",
        "/graphql",
        json={
            "data": {
                "repository": {
                    "i10": _activity_node(10, closed=True, merged_pull=21),
                    "i11": _activity_node(11, closed=False),
                }
            }
        },
    )
    created = await _ok(
        lachesis,
        "create_milestone",
        {"repo": REPO, "title": "Widgets", "goal": "Widgets ship", "spec_changes": [5]},
    )
    await _ok(lachesis, "start_milestone", {"repo": REPO, "milestone": created["id"]})

    milestones = _elements(_section(await lachesis.web_page(), "milestones"))

    [milestone] = _with(milestones, "data-milestone")
    assert milestone["data-milestone"] == "Widgets"
    assert milestone["data-goal"] == "Widgets ship"
    assert int(milestone["data-issues-done"]) == 1
    assert int(milestone["data-issues-total"]) == 2
    assert [e["data-spec-change"] for e in _with(milestones, "data-spec-change")] == ["5"]


def _activity_node(number: int, *, closed: bool, merged_pull: int | None = None) -> dict:
    """An issue as the issue-activity GraphQL query returns it."""
    stamp = (NOW - timedelta(minutes=5)).isoformat()
    pulls = []
    if merged_pull is not None:
        pulls = [
            {
                "number": merged_pull,
                "state": "MERGED",
                "merged": True,
                "updatedAt": stamp,
                "mergedAt": stamp,
                "isDraft": False,
                "commits": {"nodes": []},
            }
        ]
    return {
        "number": number,
        "state": "CLOSED" if closed else "OPEN",
        "updatedAt": stamp,
        "closedAt": stamp if closed else None,
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "closedByPullRequestsReferences": {"nodes": pulls},
    }
