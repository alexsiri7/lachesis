"""A spec change merges without the author: its pull request is opened with automerge enabled.

GitHub merges a pull request with automerge enabled once its required checks pass, the
acceptance tests later added to its branch among them, so automerge on the pull request
proposing the spec change is all that is left to Lachesis.
"""

from __future__ import annotations

from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
PULL = 180
NODE_ID = "PR_180"
TEST_ISSUE = 181

SPEC = """\
# Widgets

## Purpose

Widgets things.

## Requirements

### Requirement: Widgets are kept

The system SHALL keep widgets.

#### Scenario: Widgets kept
- GIVEN one
- WHEN it is stored
- THEN it is kept
"""


def _github_for_a_spec_change(lachesis: Lachesis) -> None:
    github = lachesis.github
    github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    github.on("GET", f"/repos/{REPO}/git/ref/heads/main", json={"object": {"sha": "abc"}})
    github.on("POST", f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/x"}, status=201)
    github.on(
        "PUT",
        f"/repos/{REPO}/contents/openspec/specs/widgets/spec.md",
        json={"content": {"sha": "def"}},
        status=201,
    )
    github.on(
        "POST",
        f"/repos/{REPO}/pulls",
        json={
            "number": PULL,
            "node_id": NODE_ID,
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
            "number": TEST_ISSUE,
            "id": 1181,
            "node_id": "I_181",
            "title": "Acceptance tests",
            "html_url": f"https://github.com/{REPO}/issues/{TEST_ISSUE}",
        },
        status=201,
    )


def _automerge_requests(lachesis: Lachesis) -> list[dict[str, Any]]:
    return [
        request.body
        for request in lachesis.github.requests_to("POST", "/graphql")
        if "enablePullRequestAutoMerge" in (request.body or {}).get("query", "")
    ]


@scenario("spec-changes", "Spec change with its tests merges by itself")
async def test_spec_change_with_its_tests_merges_by_itself(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": REPO})
    _github_for_a_spec_change(lachesis)

    result = await lachesis.call_tool(
        "propose_spec_change",
        {
            "repo": REPO,
            "title": "Keep widgets",
            "why": "Widgets need keeping.",
            "capabilities": {"widgets": SPEC},
        },
    )

    assert not result.is_error, result.text
    assert result.json()["number"] == PULL
    [automerge] = _automerge_requests(lachesis)
    assert automerge["variables"]["pullRequestId"] == NODE_ID
    assert not lachesis.github.requests_to(
        "POST", f"/repos/{REPO}/pulls/{PULL}/requested_reviewers"
    )
