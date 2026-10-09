"""A north-star audit holds the specs to the north star (spec change #319).

The contract these tests set for the implementation issues: the audit kind is
``north-star`` for ``start_audit(repo, kind)``, ``report_audit_run`` and
``read_audit_history``. Its audit issue reads ``docs/north-star.md`` and lists each story
(each ``### `` heading under ``## Stories``), and says that a gap the specs already promise is
filed as a spec-gap issue (``file_audit_finding`` with ``unmet_requirement``) while a gap they
do not is a question to the author proposing the spec change that would close it.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/kith"

NORTH_STAR = """\
# North star

What kith is for.

## Principles

- The author decides.

## Stories

### 1. Away for two weeks

I leave for two weeks and the work goes on.

### 2. A friend's birthday

I am reminded a week before, with time to send something.
"""


def _issue(number: int) -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "open",
        "labels": [{"name": "audit"}],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None,
        "state_reason": None,
    }


async def _repo_with_a_north_star(lachesis: Lachesis) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    github = lachesis.github
    github.on("GET", f"/repos/{REPO}/issues", json=[])
    github.on("GET", f"/repos/{REPO}/pulls", json=[])
    github.on("GET", f"/repos/{REPO}/contents/requirements", json=[])
    github.on(
        "GET",
        f"/repos/{REPO}/contents/openspec/specs",
        json=[{"type": "dir", "name": "reminders", "path": "openspec/specs/reminders"}],
    )
    github.on(
        "GET",
        f"/repos/{REPO}/contents/docs/north-star.md",
        json={
            "type": "file",
            "name": "north-star.md",
            "path": "docs/north-star.md",
            "sha": "abc",
            "encoding": "base64",
            "content": base64.b64encode(NORTH_STAR.encode()).decode(),
        },
    )
    github.on("GET", f"/repos/{REPO}/labels/audit", json={"name": "audit"})
    github.on("POST", f"/repos/{REPO}/issues", json=_issue(80), status=201)


@scenario("audits", "North-star audit content")
async def test_north_star_audit_content(lachesis: Lachesis) -> None:
    await _repo_with_a_north_star(lachesis)

    started = await lachesis.call_tool("start_audit", {"repo": REPO, "kind": "north-star"})

    assert not started.is_error, started.text
    [opened] = [
        request.body for request in lachesis.github.requests_to("POST", f"/repos/{REPO}/issues")
    ]
    text = " ".join(f"{opened['title']}\n{opened.get('body', '')}".lower().split())
    assert "away for two weeks" in text
    assert "a friend's birthday" in text
    assert "spec-gap" in text
    assert "unmet_requirement" in text
    assert "question to the author" in text
    assert "spec change" in text
