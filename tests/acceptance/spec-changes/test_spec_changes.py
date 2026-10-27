"""Spec changes: proposing, checking, reading and extending them, and what work landing leaves."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
PULL = 190
HEAD_REF = "spec-change/keep-backlog"

SPEC = """\
# {name}

## Purpose

{name} things.

## Requirements

### Requirement: {name} are kept

The system SHALL keep {name}.

#### Scenario: {name} kept
- GIVEN one
- WHEN it is stored
- THEN it is kept
"""

WITHOUT_A_SCENARIO = """\
# Issues

## Purpose

Issues things.

## Requirements

### Requirement: Issues are counted

The system SHALL count issues.
"""


def _spec_path(capability: str) -> str:
    return f"/repos/{REPO}/contents/openspec/specs/{capability}/spec.md"


def _uses_specs(lachesis: Lachesis) -> None:
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[
            {"type": "dir", "name": name, "path": f"openspec/specs/{name}"}
            for name in ("backlog", "issues")
        ],
    )


def _github_for_a_proposal(lachesis: Lachesis) -> None:
    github = lachesis.github
    _uses_specs(lachesis)
    github.on("GET", f"/repos/{REPO}/git/ref/heads/main", json={"object": {"sha": "abc"}})
    github.on("POST", f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/x"}, status=201)
    for name in ("backlog", "issues"):
        github.on("PUT", _spec_path(name), json={"content": {"sha": "def"}}, status=201)
    github.on(
        "POST",
        f"/repos/{REPO}/pulls",
        json={
            "number": PULL,
            "node_id": f"PR_{PULL}",
            "html_url": f"https://github.com/{REPO}/pull/{PULL}",
        },
        status=201,
    )
    github.on(
        "GET",
        f"/repos/{REPO}",
        json={
            "allow_auto_merge": True,
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "delete_branch_on_merge": True,
        },
    )
    github.on("POST", "/graphql", json={"data": {}})
    github.on("POST", f"/repos/{REPO}/issues/{PULL}/labels", json=[{"name": "spec-change"}])
    github.on("GET", f"/repos/{REPO}/labels/acceptance-tests", json={"name": "acceptance-tests"})
    github.on(
        "POST",
        f"/repos/{REPO}/issues",
        json={
            "number": PULL + 1,
            "id": 1000 + PULL + 1,
            "node_id": f"I_{PULL + 1}",
            "title": "Acceptance tests",
            "html_url": f"https://github.com/{REPO}/issues/{PULL + 1}",
        },
        status=201,
    )


def _spec_writes(lachesis: Lachesis) -> list[Any]:
    """Every request that writes a file under openspec/specs/, on any branch."""
    return [
        request
        for request in lachesis.github.requests
        if request.method in ("PUT", "DELETE")
        and request.path.startswith(f"/repos/{REPO}/contents/openspec/specs/")
    ]


async def _registered(lachesis: Lachesis) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text


@scenario("spec-changes", "Old workflow in a spec repository")
async def test_old_workflow_in_a_spec_repository(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    _uses_specs(lachesis)

    result = await lachesis.call_tool(
        "create_requirement",
        {"repo": REPO, "title": "Widgets are counted", "why": "Nobody knows.", "what": "Count."},
    )

    assert result.is_error
    assert "propose_spec_change" in result.text
    assert not [r for r in lachesis.github.requests if r.method != "GET"]


@scenario("spec-changes", "Pending edit is visible")
async def test_pending_edit_is_visible(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    github = lachesis.github
    _uses_specs(lachesis)
    spec = SPEC.format(name="Backlog")
    github.on(
        "GET",
        _spec_path("backlog"),
        json={"content": base64.b64encode(spec.encode()).decode(), "sha": "abc"},
    )
    github.on(
        "GET",
        f"/repos/{REPO}/pulls",
        json=[
            {
                "number": PULL,
                "title": "Rank the backlog",
                "html_url": f"https://github.com/{REPO}/pull/{PULL}",
                "labels": [{"name": "spec-change"}],
            }
        ],
    )
    github.on(
        "GET",
        f"/repos/{REPO}/pulls/{PULL}/files",
        json=[{"filename": "openspec/specs/backlog/spec.md", "status": "modified"}],
    )

    result = await lachesis.call_tool("get_capability", {"repo": REPO, "capability": "backlog"})

    assert not result.is_error, result.text
    capability = result.json()
    assert capability["spec"] == spec
    assert [change["number"] for change in capability["spec_changes"]] == [PULL]


@scenario("spec-changes", "Proposing a change to two capabilities")
async def test_proposing_a_change_to_two_capabilities(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    _github_for_a_proposal(lachesis)
    why = "The backlog and its issues need keeping together."

    result = await lachesis.call_tool(
        "propose_spec_change",
        {
            "repo": REPO,
            "title": "Keep the backlog and issues",
            "why": why,
            "capabilities": {
                "backlog": SPEC.format(name="Backlog"),
                "issues": SPEC.format(name="Issues"),
            },
        },
    )

    assert not result.is_error, result.text
    assert result.json()["number"] == PULL
    [pull] = lachesis.github.requests_to("POST", f"/repos/{REPO}/pulls")
    assert pull.body["body"] == why
    branches = {
        name: [request.body["branch"] for request in lachesis.github.requests_to("PUT", path)]
        for name in ("backlog", "issues")
        for path in [_spec_path(name)]
    }
    assert branches == {"backlog": [pull.body["head"]], "issues": [pull.body["head"]]}


@scenario("spec-changes", "Requirement without a scenario")
async def test_requirement_without_a_scenario(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    _github_for_a_proposal(lachesis)

    result = await lachesis.call_tool(
        "propose_spec_change",
        {
            "repo": REPO,
            "title": "Count issues",
            "why": "Issues need counting.",
            "capabilities": {"issues": WITHOUT_A_SCENARIO},
        },
    )

    assert result.is_error
    assert "issues" in result.text
    assert "Issues are counted" in result.text
    assert not lachesis.github.requests_to("POST", f"/repos/{REPO}/pulls")
    assert not _spec_writes(lachesis)


@scenario("spec-changes", "Extending an open change")
async def test_extending_an_open_change(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    github = lachesis.github
    _uses_specs(lachesis)
    github.on(
        "GET",
        f"/repos/{REPO}/pulls/{PULL}",
        json={
            "number": PULL,
            "title": "Keep the backlog",
            "html_url": f"https://github.com/{REPO}/pull/{PULL}",
            "state": "open",
            "merged": False,
            "head": {"ref": HEAD_REF},
            "base": {"ref": "main"},
            "draft": False,
            "labels": [{"name": "spec-change"}],
        },
    )
    github.on("PUT", _spec_path("issues"), json={"content": {"sha": "def"}}, status=201)

    result = await lachesis.call_tool(
        "update_spec_change",
        {"repo": REPO, "pr_number": PULL, "capabilities": {"issues": SPEC.format(name="Issues")}},
    )

    assert not result.is_error, result.text
    assert result.json()["number"] == PULL
    [write] = _spec_writes(lachesis)
    assert write.path == _spec_path("issues")
    assert write.body["branch"] == HEAD_REF
    assert not lachesis.github.requests_to("POST", f"/repos/{REPO}/pulls")


def _stamp(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _issue(number: int, body: str) -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": body,
        "state": "closed",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": _stamp(20),
        "updated_at": _stamp(1),
        "closed_at": _stamp(1),
        "state_reason": "completed",
    }


def _merged_activity(number: int) -> dict[str, Any]:
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
                    "number": 100 + number,
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


@scenario("spec-changes", "Work lands")
async def test_work_lands(lachesis: Lachesis) -> None:
    await _registered(lachesis)
    github = lachesis.github
    _uses_specs(lachesis)
    spec_change = _issue(PULL, "Keep the backlog.")
    spec_change["labels"] = [{"name": "spec-change"}]
    spec_change["pull_request"] = {
        "html_url": f"https://github.com/{REPO}/pull/{PULL}",
        "merged_at": _stamp(15),
    }
    reference = f"Build it.\n\n---\nSpec change: #{PULL}\nCapability: backlog"
    built = [_issue(n, reference) for n in (PULL + 1, PULL + 2)]
    github.on("GET", f"/repos/{REPO}/issues", json=[spec_change, *built])
    github.on(
        "POST",
        "/graphql",
        json={
            "data": {
                "repository": {
                    f"i{item['number']}": _merged_activity(item["number"]) for item in built
                }
            }
        },
    )
    github.on(
        "GET",
        f"/repos/{REPO}/commits/main/check-runs",
        json={
            "check_runs": [
                {
                    "name": "scenario-results",
                    "head_sha": "c0ffee",
                    "status": "completed",
                    "completed_at": _stamp(0),
                    "output": {"text": json.dumps({"backlog": {"Backlog kept": "passed"}})},
                }
            ]
        },
    )

    result = await lachesis.call_tool("list_spec_changes", {"repo": REPO})

    assert not result.is_error, result.text
    [entry] = result.json()
    assert entry["spec_change"] == PULL
    assert entry["derived"]["status"] == "done"
    assert not _spec_writes(lachesis)
    assert not [
        request
        for request in github.requests
        if request.method != "GET" and request.path != "/graphql"
    ]
