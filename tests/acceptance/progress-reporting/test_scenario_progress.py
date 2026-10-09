"""Progress read from scenario results: a spec change is done only when its scenarios pass.

Scenario results are what CI publishes for each default-branch commit: a ``scenario-results``
check run whose output text maps capability -> scenario -> outcome.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
NOW = datetime.now(UTC)
HEAD = "c0ffee"


def _stamp(days: int = 0) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _issue(number: int, *, body: str = "", state: str = "open") -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": body,
        "state": state,
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": _stamp(20),
        "updated_at": _stamp(1),
        "closed_at": _stamp(1) if state == "closed" else None,
        "state_reason": "completed" if state == "closed" else None,
    }


def _spec_change(number: int) -> dict[str, Any]:
    item = _issue(number, state="closed")
    item["labels"] = [{"name": "spec-change"}]
    item["pull_request"] = {
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "merged_at": _stamp(15),
    }
    return item


def _activity_node(number: int, merged_pull: int) -> dict[str, Any]:
    stamp = _stamp(1)
    return {
        "number": number,
        "state": "CLOSED",
        "updatedAt": stamp,
        "closedAt": stamp,
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "closedByPullRequestsReferences": {
            "nodes": [
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
        },
    }


def _spec_patch(capability: str, titles: list[str]) -> dict[str, Any]:
    lines = [f"+#### Scenario: {title}" for title in titles]
    return {
        "filename": f"openspec/specs/{capability}/spec.md",
        "status": "modified",
        "patch": "@@ -1,3 +1,9 @@\n" + "\n".join(lines),
    }


def _publish(lachesis: Lachesis, results: dict[str, dict[str, str]]) -> None:
    """The scenario-results check run CI published for the default branch's head."""
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/branches/main",
        json={"name": "main", "commit": {"sha": HEAD}},
    )
    lachesis.github.on("GET", f"/repos/{REPO}/git/ref/heads/main", json={"object": {"sha": HEAD}})
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/commits/{HEAD}/check-runs",
        json={
            "total_count": 1,
            "check_runs": [
                {
                    "name": "scenario-results",
                    "head_sha": HEAD,
                    "status": "completed",
                    "conclusion": "success",
                    "completed_at": _stamp(),
                    "output": {
                        "title": "Scenario results",
                        "text": json.dumps(results),
                    },
                }
            ],
        },
    )
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/commits/main/check-runs",
        json={
            "total_count": 1,
            "check_runs": [
                {
                    "name": "scenario-results",
                    "head_sha": HEAD,
                    "status": "completed",
                    "conclusion": "success",
                    "completed_at": _stamp(),
                    "output": {
                        "title": "Scenario results",
                        "text": json.dumps(results),
                    },
                }
            ],
        },
    )


async def _built_spec_change(lachesis: Lachesis, number: int, scenarios: list[str]) -> None:
    """Spec change *number* merged, adding *scenarios* to widgets; three issues built it."""
    github = lachesis.github
    await lachesis.call_tool("register_repo", {"repo": REPO})
    github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    github.on("GET", f"/repos/{REPO}/contents/requirements", json=[])
    reference = f"Build it.\n\n---\nSpec change: #{number}\nCapability: widgets"
    built = [
        _issue(n, body=reference, state="closed") for n in (number + 1, number + 2, number + 3)
    ]
    items = [_spec_change(number), *built]
    github.on("GET", f"/repos/{REPO}/issues", json=items)
    for item in items:
        github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)
    github.on("GET", f"/repos/{REPO}/pulls", json=[])
    github.on(
        "GET",
        f"/repos/{REPO}/pulls/{number}/files",
        json=[_spec_patch("widgets", scenarios)],
    )
    github.on(
        "POST",
        "/graphql",
        json={
            "data": {
                "repository": {
                    f"i{item['number']}": _activity_node(item["number"], 100 + item["number"])
                    for item in built
                }
            }
        },
    )


async def _dashboard(lachesis: Lachesis) -> dict[str, Any]:
    result = await lachesis.call_tool("get_dashboard", {})
    assert not result.is_error, result.text
    return result.json()


def _spec_change_entry(dashboard: dict[str, Any], number: int) -> dict[str, Any]:
    [listing] = [repo for repo in dashboard["repos"] if repo["repo"] == REPO]
    [entry] = [change for change in listing["spec_changes"] if change["spec_change"] == number]
    return entry


@scenario("progress-reporting", "Spec change fully built")
async def test_spec_change_fully_built(lachesis: Lachesis) -> None:
    scenarios = ["Widget kept", "Widget found", "Widget counted"]
    await _built_spec_change(lachesis, 152, scenarios)
    _publish(lachesis, {"widgets": {title: "passed" for title in scenarios}})

    entry = _spec_change_entry(await _dashboard(lachesis), 152)

    assert entry["derived"]["status"] == "done"


@scenario("progress-reporting", "Built but not yet passing")
async def test_built_but_not_yet_passing(lachesis: Lachesis) -> None:
    scenarios = ["Widget kept", "Widget found", "Widget counted"]
    await _built_spec_change(lachesis, 170, scenarios)
    _publish(
        lachesis,
        {
            "widgets": {
                "Widget kept": "passed",
                "Widget found": "passed",
                "Widget counted": "xfail",
            }
        },
    )

    entry = _spec_change_entry(await _dashboard(lachesis), 170)

    assert entry["derived"]["status"] == "in-progress"
    assert "Widget counted" in json.dumps(entry)
    assert "Widget kept" not in json.dumps(entry)


def _string_lists(value: Any) -> Iterator[list[str]]:
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _string_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _string_lists(child)


def _capability_views(value: Any, capability: str) -> Iterator[dict[str, Any]]:
    """Every object in *value* that stands for *capability*: keyed by it or naming it."""
    if isinstance(value, dict):
        if value.get("capability") == capability:
            yield value
        for key, child in value.items():
            if key == capability and isinstance(child, dict):
                yield child
            yield from _capability_views(child, capability)
    elif isinstance(value, list):
        for child in value:
            yield from _capability_views(child, capability)


@scenario("progress-reporting", "Capability view")
async def test_capability_view(lachesis: Lachesis) -> None:
    passing = ["Ordered", "Focused", "Interrupted", "Drawn", "Held"]
    pending = ["Parked"]
    await lachesis.call_tool("register_repo", {"repo": REPO})
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "backlog", "path": "openspec/specs/backlog"}],
    )
    lachesis.github.on("GET", f"/repos/{REPO}/contents/requirements", json=[])
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=[])
    lachesis.github.on("GET", f"/repos/{REPO}/pulls", json=[])
    _publish(
        lachesis,
        {"backlog": {**{title: "passed" for title in passing}, "Parked": "xfail"}},
    )

    dashboard = await _dashboard(lachesis)

    views = list(_capability_views(dashboard, "backlog"))
    assert views, "no view of the backlog capability"
    lists = [sorted(names) for view in views for names in _string_lists(view)]
    assert sorted(passing) in lists
    assert sorted(pending) in lists
