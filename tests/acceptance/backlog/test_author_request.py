"""The author's requests rank high: work on a spec change the author asked for comes early.

The contract these tests set for the implementation issues of spec change #319:

- ``propose_spec_change(..., author_request: bool = False)``: ``True`` marks the spec change
  as the author's request and labels its pull request ``author-request``.
- The backlog puts open issues implementing a merged ``author-request`` spec change in a
  tier below focus and above milestone work, for two sprints after the spec change merges.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
NOW = datetime.now(UTC)
SPEC_CHANGE = 5


def _issue(number: int, *, age: timedelta, body: str = "") -> dict[str, Any]:
    stamp = (NOW - age).isoformat()
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": body,
        "state": "open",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": stamp,
        "updated_at": stamp,
        "closed_at": None,
        "state_reason": None,
    }


def _merged_author_request(number: int, *, merged: timedelta) -> tuple[dict, dict]:
    """The spec change's pull request, as the issues API and the pulls API show it."""
    merged_at = (NOW - merged).isoformat()
    labels = [{"name": "spec-change"}, {"name": "author-request"}]
    url = f"https://github.com/{REPO}/pull/{number}"
    as_issue = {
        **_issue(number, age=merged + timedelta(hours=2)),
        "title": "A feature the author asked for",
        "state": "closed",
        "labels": labels,
        "closed_at": merged_at,
        "state_reason": "completed",
        "pull_request": {"html_url": url, "merged_at": merged_at},
    }
    as_pull = {
        "number": number,
        "node_id": f"PR_{number}",
        "title": as_issue["title"],
        "html_url": url,
        "state": "closed",
        "merged": True,
        "merged_at": merged_at,
        "mergeable": None,
        "mergeable_state": None,
        "head": {"ref": "spec-change/feature"},
        "base": {"ref": "main"},
        "draft": False,
        "labels": labels,
        "created_at": as_issue["created_at"],
        "closed_at": merged_at,
    }
    return as_issue, as_pull


@pytest.mark.xfail(strict=True, reason="spec change #319")
@scenario("backlog", "The author's request ranks high")
async def test_the_authors_request_ranks_high(lachesis: Lachesis) -> None:
    spec_change, pull = _merged_author_request(SPEC_CHANGE, merged=timedelta(days=1))
    implementing = _issue(
        10,
        age=timedelta(hours=20),
        body=f"Build it.\n\n---\nSpec change: #{SPEC_CHANGE}\nCapability: widgets",
    )
    older = _issue(1, age=timedelta(days=5))
    items = [spec_change, implementing, older]
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "widgets", "path": "openspec/specs/widgets"}],
    )
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=items)
    for item in items:
        lachesis.github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)
    lachesis.github.on("GET", f"/repos/{REPO}/pulls/{SPEC_CHANGE}", json=pull)
    lachesis.github.on("GET", f"/repos/{REPO}/pulls", json=[pull])

    result = await lachesis.call_tool("get_backlog", {"repo": REPO})

    assert not result.is_error, result.text
    order = [entry["number"] for entry in result.json()]
    assert order == [10, 1]
