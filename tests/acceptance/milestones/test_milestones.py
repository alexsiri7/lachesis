"""Milestones: spec changes grouped, their state read from the work, their gaps reviewed.

A spec change's issues are those whose reference block names it; whether each landed is what
GitHub's issue activity says, and whether its merge is live is what the production deployments
of that merge commit say.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, Notification, scenario
from harness.github import NOT_FOUND, RecordedRequest

REPO = "owner/repo"
NOW = datetime.now(UTC)
SPEC_PATH = "openspec/specs/widgets/spec.md"
BASE = "ba5e"
HEAD = "4ead"
COUNTED = "Widgets are counted"
KEPT = "Widgets are kept"
WIDGETS_SPEC = f"""# Widgets

## Requirements

### Requirement: {COUNTED}

The system SHALL count widgets.

#### Scenario: Counting
- WHEN widgets are counted
- THEN the count is returned

### Requirement: {KEPT}

The system SHALL keep widgets.

#### Scenario: Keeping
- WHEN a widget is kept
- THEN it is still there
"""


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _issue(number: int, *, state: str = "open", body: str = "") -> dict[str, Any]:
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
        "created_at": (NOW - timedelta(days=20)).isoformat(),
        "updated_at": (NOW - timedelta(days=1)).isoformat(),
        "closed_at": (NOW - timedelta(days=1)).isoformat() if state == "closed" else None,
        "state_reason": "completed" if state == "closed" else None,
    }


def _spec_change(number: int, *, merged: bool) -> dict[str, Any]:
    pull = {"html_url": f"https://github.com/{REPO}/pull/{number}"}
    if merged:
        pull["merged_at"] = (NOW - timedelta(days=15)).isoformat()
    return {
        **_issue(number, state="closed" if merged else "open"),
        "labels": [{"name": "spec-change"}],
        "pull_request": pull,
    }


def _implementing(spec_change: int, *, requirement: str | None = None) -> str:
    lines = ["Build it.", "", "---", f"Spec change: #{spec_change}", "Capability: widgets"]
    if requirement is not None:
        lines.append(f"Requirement: {requirement}")
    return "\n".join(lines)


def _open(number: int) -> dict[str, Any]:
    """Issue *number* as the issue-activity query returns it, still open."""
    stamp = (NOW - timedelta(days=1)).isoformat()
    return {
        "number": number,
        "state": "OPEN",
        "updatedAt": stamp,
        "closedAt": None,
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "closedByPullRequestsReferences": {"nodes": []},
    }


def _closed_unmerged(number: int) -> dict[str, Any]:
    return {**_open(number), "state": "CLOSED", "closedAt": (NOW - timedelta(days=1)).isoformat()}


def _merged(number: int, *, days_ago: int = 1) -> dict[str, Any]:
    """Issue *number*, closed by its merged pull request, whose merge commit is c<number>."""
    stamp = (NOW - timedelta(days=days_ago)).isoformat()
    return {
        **_closed_unmerged(number),
        "closedAt": stamp,
        "closedByPullRequestsReferences": {
            "nodes": [
                {
                    "number": 100 + number,
                    "state": "MERGED",
                    "merged": True,
                    "updatedAt": stamp,
                    "mergedAt": stamp,
                    "mergeCommit": {"oid": f"c{number}"},
                    "isDraft": False,
                    "commits": {"nodes": []},
                }
            ]
        },
    }


async def _repo(
    lachesis: Lachesis, items: list[dict[str, Any]], activity: Sequence[dict[str, Any]] = ()
) -> None:
    """A registered repository that uses specs, holding *items*, its issues' *activity* read."""
    await _ok(lachesis, "register_repo", {"repo": REPO})
    github = lachesis.github
    github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    github.on("GET", f"/repos/{REPO}/issues", json=items)
    for item in items:
        github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)
    github.on(
        "POST",
        "/graphql",
        json={"data": {"repository": {f"i{node['number']}": node for node in activity}}},
    )


def _scenarios_pass(lachesis: Lachesis) -> None:
    """CI published scenario results for the default branch in which every scenario passes."""
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/commits/main/check-runs",
        json={
            "total_count": 1,
            "check_runs": [
                {
                    "name": "scenario-results",
                    "head_sha": "main-head",
                    "status": "completed",
                    "conclusion": "success",
                    "completed_at": NOW.isoformat(),
                    "output": {"text": json.dumps({"widgets": {"Counting": "passed"}})},
                }
            ],
        },
    )


def _production(lachesis: Lachesis, outcome: str | None) -> None:
    """Production's deployment of the last merge ended in *outcome*; None when there is none."""
    deployments = (
        []
        if outcome is None
        else [
            {"id": 1, "environment": "production", "production_environment": True, "task": "deploy"}
        ]
    )
    lachesis.github.on("GET", f"/repos/{REPO}/deployments", json=deployments)
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/deployments/1/statuses",
        json=[{"state": outcome, "log_url": f"https://github.com/{REPO}/actions/runs/9"}],
    )


async def _milestone(lachesis: Lachesis, title: str, spec_changes: list[int]) -> int:
    created = await _ok(
        lachesis,
        "create_milestone",
        {"repo": REPO, "title": title, "goal": f"{title} ship", "spec_changes": spec_changes},
    )
    return int(created["id"])


async def _active_milestone(lachesis: Lachesis, title: str, spec_changes: list[int]) -> int:
    milestone = await _milestone(lachesis, title, spec_changes)
    await _ok(lachesis, "start_milestone", {"repo": REPO, "milestone": milestone})
    return milestone


async def _read(lachesis: Lachesis, milestone: int) -> dict[str, Any]:
    return await _ok(lachesis, "get_milestone", {"repo": REPO, "milestone": milestone})


async def _review(lachesis: Lachesis, milestone: int) -> dict[str, Any]:
    return await _ok(lachesis, "review_milestone", {"repo": REPO, "milestone": milestone})


async def _built(lachesis: Lachesis) -> int:
    """An active milestone whose spec change 170 merged, and both its issues with it."""
    await _repo(
        lachesis,
        [
            _spec_change(170, merged=True),
            _issue(11, state="closed", body=_implementing(170)),
            _issue(12, state="closed", body=_implementing(170)),
        ],
        [_merged(11, days_ago=2), _merged(12)],
    )
    _scenarios_pass(lachesis)
    _production(lachesis, None)
    return await _active_milestone(lachesis, "Widgets", [170])


@scenario("milestones", "Creating a milestone")
async def test_creating_a_milestone(lachesis: Lachesis) -> None:
    await _repo(lachesis, [_spec_change(170, merged=False), _spec_change(172, merged=False)])

    milestone = await _milestone(lachesis, "Widgets", [170, 172])

    read = await _read(lachesis, milestone)
    assert read["title"] == "Widgets"
    assert read["goal"] == "Widgets ship"
    assert sorted(read["spec_changes"]) == [170, 172]


@scenario("milestones", "Already in another milestone")
async def test_already_in_another_milestone(lachesis: Lachesis) -> None:
    await _repo(lachesis, [_spec_change(170, merged=False)])
    first = await _milestone(lachesis, "Milestone A", [170])
    second = await _milestone(lachesis, "Milestone B", [])

    added = await lachesis.call_tool(
        "add_milestone_spec_change", {"repo": REPO, "milestone": second, "spec_change": 170}
    )

    assert added.is_error
    assert "Milestone A" in added.text
    assert (await _read(lachesis, second))["spec_changes"] == []
    assert (await _read(lachesis, first))["spec_changes"] == [170]


@scenario("milestones", "Merged but not yet live")
async def test_merged_but_not_yet_live(lachesis: Lachesis) -> None:
    milestone = await _built(lachesis)
    _production(lachesis, "pending")

    assert (await _read(lachesis, milestone))["state"] == "verifying"


@scenario("milestones", "Live")
async def test_live(lachesis: Lachesis) -> None:
    milestone = await _built(lachesis)
    assert (await _read(lachesis, milestone))["state"] == "verifying"

    _production(lachesis, "success")

    assert (await _read(lachesis, milestone))["state"] == "done"


def _spec_at_merge(request: RecordedRequest) -> tuple[int, Any]:
    """The widgets spec as spec change 170 left it; before it, there was none."""
    if request.params.get("ref") == HEAD:
        return 200, WIDGETS_SPEC.encode()
    return 404, NOT_FOUND


@scenario("milestones", "Requirement without work")
async def test_requirement_without_work(lachesis: Lachesis) -> None:
    await _repo(
        lachesis,
        [_spec_change(170, merged=True), _issue(11, body=_implementing(170, requirement=COUNTED))],
        [_open(11)],
    )
    github = lachesis.github
    github.on(
        "GET",
        f"/repos/{REPO}/pulls/170",
        json={"number": 170, "base": {"sha": "main-before"}, "head": {"sha": HEAD}},
    )
    github.on(
        "GET",
        f"/repos/{REPO}/compare/main-before...{HEAD}",
        json={"merge_base_commit": {"sha": BASE}},
    )
    github.on(
        "GET",
        f"/repos/{REPO}/pulls/170/files",
        json=[{"filename": SPEC_PATH, "status": "added", "patch": None}],
    )
    github.serve(f"/repos/{REPO}/contents/{SPEC_PATH}", _spec_at_merge)
    milestone = await _active_milestone(lachesis, "Widgets", [170])

    review = await _review(lachesis, milestone)

    gaps = review["gaps"]["requirements_without_issues"]
    assert [(gap["spec_change"], gap["requirement"]) for gap in gaps] == [(170, KEPT)]


@scenario("milestones", "Work dropped")
async def test_work_dropped(lachesis: Lachesis) -> None:
    await _repo(
        lachesis,
        [
            _spec_change(170, merged=False),
            _issue(11, state="closed", body=_implementing(170)),
            _issue(12, body=_implementing(170)),
        ],
        [_closed_unmerged(11), _open(12)],
    )
    milestone = await _active_milestone(lachesis, "Widgets", [170])

    review = await _review(lachesis, milestone)

    dropped = review["gaps"]["issues_closed_without_merge"]
    assert [gap["issue"] for gap in dropped] == [11]


@scenario("milestones", "Owner guidance")
async def test_owner_guidance(lachesis: Lachesis) -> None:
    await _repo(lachesis, [])

    guidance = await _ok(lachesis, "get_initial_instructions", {"repo": REPO})

    text = " ".join(guidance.lower().split())
    assert "milestone owner" in text
    assert "review_milestone" in text
    assert "never edits the spec" in text


def _milestone_notifications(lachesis: Lachesis) -> list[Notification]:
    return [n for n in lachesis.ntfy.notifications if "Widgets" in n.title]


@scenario("milestones", "Milestone goes live")
async def test_milestone_goes_live(lachesis: Lachesis) -> None:
    await _ok(lachesis, "set_ntfy_config", {"topic": "lachesis-author"})
    milestone = await _built(lachesis)
    assert (await _read(lachesis, milestone))["state"] == "verifying"
    assert _milestone_notifications(lachesis) == []

    _production(lachesis, "success")
    await _read(lachesis, milestone)
    await _read(lachesis, milestone)

    [notification] = _milestone_notifications(lachesis)
    assert "done" in notification.title.lower()
    assert "available" in notification.body.lower()
    notes = await _ok(lachesis, "list_notes", {"repo": REPO, "milestone": str(milestone)})
    assert [note["type"] for note in notes] == ["report"]
