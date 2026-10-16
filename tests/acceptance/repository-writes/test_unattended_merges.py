"""Repository writes: a pull request Lachesis opens merges by itself, or is Lachesis's fault.

GitHub answers a direct commit on a protected default branch with 409. The stub matches
requests by path alone, and a commit to the Lachesis branch has the same contents path, so
the stub answers that path's writes in turn: refused on main, then accepted.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from harness import Lachesis, find_matching, scenario

REPO = "owner/widgets"
REQUIREMENTS = f"/repos/{REPO}/contents/requirements"
PULL = 42
NODE_ID = "PR_42"
PULL_URL = f"https://github.com/{REPO}/pull/{PULL}"
PROTECTED = {"message": "Changes must be made through a pull request.", "status": "409"}
AUTOMERGE_REFUSAL = "Auto merge is not allowed for this repository"

REQUIREMENT_FILE = "001-widgets-are-counted.md"
REQUIREMENT = """\
---
id: '001'
title: Widgets are counted
status: in-progress
github_issue: null
created: '2026-10-01'
updated: '2026-10-01'
---
## Why

Nobody knows how many widgets there are.

## What

Count them.

## Issues

_None yet._
"""


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


def _names_the_pull(entry: dict[str, Any]) -> bool:
    return entry.get("number") == PULL or entry.get("url") == PULL_URL


def _about_the_pull(entry: dict[str, Any]) -> bool:
    """*entry* names the pull request itself, or holds it as one of its fields."""
    return _names_the_pull(entry) or any(
        isinstance(value, dict) and _names_the_pull(value) for value in entry.values()
    )


def _github_holds(lachesis: Lachesis, *names: str) -> None:
    """The requirements directory holds *names*, each with REQUIREMENT as its content."""
    github = lachesis.github
    github.on(
        "GET",
        REQUIREMENTS,
        json=[{"type": "file", "name": name, "path": f"requirements/{name}"} for name in names],
    )
    for name in names:
        github.on(
            "GET",
            f"{REQUIREMENTS}/{name}",
            json={
                "type": "file",
                "path": f"requirements/{name}",
                "sha": f"sha-{name}",
                "encoding": "base64",
                "content": base64.b64encode(REQUIREMENT.encode()).decode(),
            },
        )


def _main_is_protected(lachesis: Lachesis, name: str) -> None:
    """Main refuses each of the three attempts an update makes there; its branch takes it.

    Three is `_MAX_WRITE_ATTEMPTS`, which a unit test in mcp/tests/test_requirements.py pins.
    """
    lachesis.github.on_sequence(
        "PUT",
        f"{REQUIREMENTS}/{name}",
        *[(409, PROTECTED)] * 3,
        (200, {"content": {"sha": "sha-written"}}),
    )


def _github_opens_pull_requests(lachesis: Lachesis) -> None:
    github = lachesis.github
    github.on("GET", f"/repos/{REPO}/pulls", json=[])
    github.on("GET", f"/repos/{REPO}/git/ref/heads/main", json={"object": {"sha": "abc"}})
    github.on("POST", f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/x"}, status=201)
    github.on(
        "POST",
        f"/repos/{REPO}/pulls",
        json={"number": PULL, "node_id": NODE_ID, "html_url": PULL_URL},
        status=201,
    )
    github.on(
        "GET",
        f"/repos/{REPO}",
        json={
            "full_name": REPO,
            "default_branch": "main",
            "allow_auto_merge": True,
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "delete_branch_on_merge": True,
        },
    )


@scenario("repository-writes", "Status update merges by itself")
async def test_status_update_merges_by_itself(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO})
    _github_holds(lachesis, REQUIREMENT_FILE)
    _main_is_protected(lachesis, REQUIREMENT_FILE)
    _github_opens_pull_requests(lachesis)
    lachesis.github.on("POST", "/graphql", json={"data": {}})

    updated = await _ok(
        lachesis, "update_requirement", {"repo": REPO, "id": "001", "fields": {"status": "done"}}
    )

    assert updated["pr"]["number"] == PULL
    [automerge] = lachesis.github.automerge_requests()
    assert automerge["variables"]["pullRequestId"] == NODE_ID
    [opened] = lachesis.github.requests_to("POST", f"/repos/{REPO}/pulls")
    assert "review and merge" not in opened.body["body"].lower(), opened.body["body"]
    assert not lachesis.github.requests_to(
        "POST", f"/repos/{REPO}/pulls/{PULL}/requested_reviewers"
    )
    assert not lachesis.ntfy.notifications


@scenario("repository-writes", "Automerge refused")
async def test_automerge_refused(lachesis: Lachesis) -> None:
    await _ok(lachesis, "register_repo", {"repo": REPO})
    _github_holds(lachesis, REQUIREMENT_FILE)
    _main_is_protected(lachesis, REQUIREMENT_FILE)
    _github_opens_pull_requests(lachesis)
    lachesis.github.on(
        "POST",
        "/graphql",
        json={"data": None, "errors": [{"type": "UNPROCESSABLE", "message": AUTOMERGE_REFUSAL}]},
    )

    result = await lachesis.call_tool(
        "update_requirement", {"repo": REPO, "id": "001", "fields": {"status": "done"}}
    )

    assert not result.is_error, result.text
    told = result.text.lower()
    assert "automerge" in told or "auto-merge" in told, result.text
    assert AUTOMERGE_REFUSAL.lower() in told, result.text
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/pulls",
        json=[
            {
                "number": PULL,
                "html_url": PULL_URL,
                "head": {"ref": "lachesis/req-001-20261012T090000Z"},
                "created_at": "2026-10-12T09:00:00Z",
            }
        ],
    )
    dashboard = await _ok(lachesis, "get_dashboard", {})
    faults = find_matching(
        dashboard,
        lambda d: (
            AUTOMERGE_REFUSAL.lower() in json.dumps(d).lower()
            and "fault" in json.dumps(d).lower()
            and _about_the_pull(d)
        ),
    )
    assert faults, dashboard
    assert not find_matching(dashboard["waiting_on_author"], _names_the_pull), dashboard
