"""A spec change brings its own acceptance tests: proposing one opens the issue that writes them."""

from __future__ import annotations

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
PULL = 170
TEST_ISSUE = 171

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


def _github_for_a_spec_change(lachesis: Lachesis) -> None:
    github = lachesis.github
    github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    github.on("GET", f"/repos/{REPO}/git/ref/heads/main", json={"object": {"sha": "abc"}})
    github.on("POST", f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/x"}, status=201)
    for name in ("widgets", "gadgets"):
        path = f"/repos/{REPO}/contents/openspec/specs/{name}/spec.md"
        github.on("PUT", path, json={"content": {"sha": "def"}}, status=201)
    github.on(
        "POST",
        f"/repos/{REPO}/pulls",
        json={
            "number": PULL,
            "node_id": "PR_170",
            "html_url": f"https://github.com/{REPO}/pull/{PULL}",
        },
        status=201,
    )
    github.on(
        "GET",
        f"/repos/{REPO}",
        json={
            "allow_auto_merge": False,
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "delete_branch_on_merge": True,
        },
    )
    github.on("POST", f"/repos/{REPO}/issues/{PULL}/labels", json=[{"name": "spec-change"}])
    github.on(
        "GET",
        f"/repos/{REPO}/labels/acceptance-tests",
        json={"name": "acceptance-tests"},
    )
    github.on(
        "POST",
        f"/repos/{REPO}/issues",
        json={
            "number": TEST_ISSUE,
            "id": 1171,
            "node_id": "I_171",
            "title": "Acceptance tests",
            "html_url": f"https://github.com/{REPO}/issues/{TEST_ISSUE}",
        },
        status=201,
    )


@pytest.mark.xfail(strict=True, reason="spec change #173")
@scenario("spec-changes", "Proposing opens the test issue")
async def test_proposing_opens_the_test_issue(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": REPO})
    _github_for_a_spec_change(lachesis)

    result = await lachesis.call_tool(
        "propose_spec_change",
        {
            "repo": REPO,
            "title": "Keep widgets and gadgets",
            "why": "Both need keeping.",
            "capabilities": {
                "widgets": SPEC.format(name="Widgets"),
                "gadgets": SPEC.format(name="Gadgets"),
            },
        },
    )

    assert not result.is_error, result.text
    opened = [
        request.body
        for request in lachesis.github.requests_to("POST", f"/repos/{REPO}/issues")
        if "acceptance-tests" in (request.body or {}).get("labels", [])
    ]
    assert len(opened) == 1
    issue = opened[0]
    text = f"{issue['title']}\n{issue.get('body', '')}"
    assert f"#{PULL}" in text
    assert "widgets" in text
    assert "gadgets" in text
    assert str(TEST_ISSUE) in result.text
    assert result.json()["number"] == PULL
