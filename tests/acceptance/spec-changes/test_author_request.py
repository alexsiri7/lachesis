"""A spec change the author asked for in conversation is marked as the author's request.

The contract these tests set for the implementation issues of spec change #319:
``propose_spec_change(repo, title, why, capabilities, author_request: bool = False)``;
``author_request=True`` labels the spec change's pull request ``author-request`` as well as
``spec-change``. Spec changes proposed by an agent or an audit leave it false.
"""

from __future__ import annotations

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
PULL = 180

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
    path = f"/repos/{REPO}/contents/openspec/specs/widgets/spec.md"
    github.on("PUT", path, json={"content": {"sha": "def"}}, status=201)
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
    github.on("POST", f"/repos/{REPO}/issues/{PULL}/labels", json=[{"name": "spec-change"}])
    for label in ("spec-change", "author-request", "acceptance-tests"):
        github.on("GET", f"/repos/{REPO}/labels/{label}", json={"name": label})
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
    github.on("POST", "/graphql", json={"data": {}})


def _labels_on_the_pull(lachesis: Lachesis) -> set[str]:
    labelled = lachesis.github.requests_to("POST", f"/repos/{REPO}/issues/{PULL}/labels")
    return {label for request in labelled for label in (request.body or {}).get("labels", [])}


@pytest.mark.xfail(strict=True, reason="spec change #319")
@scenario("spec-changes", "The author asks in conversation")
async def test_the_author_asks_in_conversation(lachesis: Lachesis) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    _github_for_a_spec_change(lachesis)

    result = await lachesis.call_tool(
        "propose_spec_change",
        {
            "repo": REPO,
            "title": "Keep widgets",
            "why": "The author asked for widgets to be kept.",
            "capabilities": {"widgets": SPEC},
            "author_request": True,
        },
    )

    assert not result.is_error, result.text
    assert result.json()["number"] == PULL
    assert {"spec-change", "author-request"} <= _labels_on_the_pull(lachesis)
