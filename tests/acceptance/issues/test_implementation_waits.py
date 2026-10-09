"""Implementation waits for its tests: an implementing issue is blocked by the tests issue."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
SPEC_CHANGE = 170
TEST_ISSUE = 171
NEW_ISSUE = 180
NOW = datetime.now(UTC).isoformat()


def _issue(number: int, *, labels: tuple[str, ...] = (), body: str = "") -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": body,
        "state": "open",
        "labels": [{"name": label} for label in labels],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": NOW,
        "updated_at": NOW,
        "closed_at": None,
        "state_reason": None,
    }


@pytest.mark.xfail(strict=True, reason="spec change #173")
@scenario("issues", "Implementation issue created")
async def test_implementation_issue_created(lachesis: Lachesis) -> None:
    github = lachesis.github
    await lachesis.call_tool("register_repo", {"repo": REPO})
    github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    spec_change = {
        **_issue(SPEC_CHANGE, labels=("spec-change",)),
        "pull_request": {"html_url": f"https://github.com/{REPO}/pull/{SPEC_CHANGE}"},
    }
    tests_issue = _issue(
        TEST_ISSUE,
        labels=("acceptance-tests",),
        body=f"Write the tests.\n\n---\nSpec change: #{SPEC_CHANGE}\nCapability: widgets",
    )
    github.on("GET", f"/repos/{REPO}/issues", json=[spec_change, tests_issue])
    github.on("GET", f"/repos/{REPO}/issues/{SPEC_CHANGE}", json=spec_change)
    github.on("GET", f"/repos/{REPO}/issues/{TEST_ISSUE}", json=tests_issue)
    github.on("POST", f"/repos/{REPO}/issues", json=_issue(NEW_ISSUE), status=201)
    github.on("GET", f"/repos/{REPO}/issues/{NEW_ISSUE}", json=_issue(NEW_ISSUE))
    github.on(
        "POST",
        f"/repos/{REPO}/issues/{NEW_ISSUE}/dependencies/blocked_by",
        json=tests_issue,
        status=201,
    )

    result = await lachesis.call_tool(
        "create_github_issues",
        {
            "repo": REPO,
            "issues": [
                {
                    "title": "Keep widgets",
                    "body": "Build it.",
                    "capability": "widgets",
                    "spec_change": SPEC_CHANGE,
                }
            ],
        },
    )

    assert not result.is_error, result.text
    blocked = github.requests_to(
        "POST", f"/repos/{REPO}/issues/{NEW_ISSUE}/dependencies/blocked_by"
    )
    assert [request.body["issue_id"] for request in blocked] == [1000 + TEST_ISSUE]
