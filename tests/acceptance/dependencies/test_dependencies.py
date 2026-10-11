"""Dependencies: an issue declared blocked by another, read back from either side."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
BLOCKER = 50
BLOCKED = 51
NOW = datetime.now(UTC).isoformat()


def _issue(number: int) -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "open",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": NOW,
        "updated_at": NOW,
        "closed_at": None,
        "state_reason": None,
    }


def _stub_dependency_on_github(lachesis: Lachesis) -> None:
    """GitHub holding 51 blocked by 50, as seen from both issues."""
    github = lachesis.github
    issues = f"/repos/{REPO}/issues"
    github.on(
        "GET", f"{issues}/{BLOCKED}/dependencies/blocked_by", json=[_issue(BLOCKER)]
    )
    github.on("GET", f"{issues}/{BLOCKED}/dependencies/blocking", json=[])
    github.on("GET", f"{issues}/{BLOCKER}/dependencies/blocked_by", json=[])
    github.on(
        "GET", f"{issues}/{BLOCKER}/dependencies/blocking", json=[_issue(BLOCKED)]
    )


@scenario("dependencies", "Declare a dependency")
async def test_declare_a_dependency(lachesis: Lachesis) -> None:
    github = lachesis.github
    await lachesis.call_tool("register_repo", {"repo": REPO})
    github.on("GET", f"/repos/{REPO}/issues/{BLOCKER}", json=_issue(BLOCKER))
    github.on("GET", f"/repos/{REPO}/issues/{BLOCKED}", json=_issue(BLOCKED))
    github.on(
        "POST",
        f"/repos/{REPO}/issues/{BLOCKED}/dependencies/blocked_by",
        json=_issue(BLOCKER),
        status=201,
    )
    _stub_dependency_on_github(lachesis)

    result = await lachesis.call_tool(
        "add_dependency",
        {"repo": REPO, "issue_number": BLOCKED, "blocked_by_issue_number": BLOCKER},
    )

    assert not result.is_error, result.text
    assert result.json()["blocked_by"] == [BLOCKER]
    declared = github.requests_to(
        "POST", f"/repos/{REPO}/issues/{BLOCKED}/dependencies/blocked_by"
    )
    assert [request.body["issue_id"] for request in declared] == [_issue(BLOCKER)["id"]]


@scenario("dependencies", "Both directions")
async def test_both_directions(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": REPO})
    _stub_dependency_on_github(lachesis)

    result = await lachesis.call_tool(
        "get_dependencies", {"repo": REPO, "issue_number": BLOCKER}
    )

    assert not result.is_error, result.text
    assert result.json()["blocking"] == [BLOCKED]
