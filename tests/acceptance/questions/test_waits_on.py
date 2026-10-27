"""A question waiting on another repository's issues settles when they close (issue #403).

The contract these tests set for the implementation:

- ``ask_question`` takes ``waits_on``: issues in other repositories, each ``{repo, issue}``.
  Reading the open questions reads each one; when every one is closed, an answer naming
  them as closed is recorded with outcome ``none``, noted on each issue the question blocks,
  and those issues lose ``needs-author``.
- A question waiting on an issue in its own repository is refused, naming add_dependency.
- The agent guidance tells a run blocked on an issue in the same repository to call
  add_dependency instead of asking.
"""

from __future__ import annotations

import re
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
OTHER = "other/repo"


def _issue(repo: str, number: int, state: str = "open") -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": state,
        "labels": [{"name": "needs-author"}] if repo == REPO else [],
        "assignees": [],
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None if state == "open" else "2026-01-02T00:00:00+00:00",
        "state_reason": None if state == "open" else "completed",
    }


async def _repo(lachesis: Lachesis, number: int) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    github = lachesis.github
    github.on("GET", "/user", json={"login": "the-author"})
    github.on("GET", f"/repos/{REPO}/issues", json=[_issue(REPO, number)])
    github.on("POST", f"/repos/{REPO}/labels", json={"name": "needs-author"}, status=201)
    github.on("GET", f"/repos/{REPO}/issues/{number}", json=_issue(REPO, number))
    github.on("GET", f"/repos/{REPO}/issues/{number}/comments", json=[])
    github.on(
        "POST",
        f"/repos/{REPO}/issues/{number}/comments",
        json={"id": number, "html_url": f"https://github.com/{REPO}/issues/{number}#c"},
        status=201,
    )
    github.on("POST", f"/repos/{REPO}/issues/{number}/labels", json=[{"name": "needs-author"}])
    github.on("DELETE", f"/repos/{REPO}/issues/{number}/labels/needs-author", json=[])


def _waited_on(lachesis: Lachesis, number: int, state: str = "open") -> None:
    lachesis.github.on("GET", f"/repos/{OTHER}/issues/{number}", json=_issue(OTHER, number, state))


async def _ask_waiting(lachesis: Lachesis) -> int:
    asked = await lachesis.call_tool(
        "ask_question",
        {
            "repo": REPO,
            "question": "Can the export ship before other/repo#301 lands?",
            "kind": "clarification",
            "asker": "agent",
            "blocks": [161],
            "waits_on": [{"repo": OTHER, "issue": 301}],
        },
    )
    assert not asked.is_error, asked.text
    return int(asked.json()["id"])


async def _open_ids(lachesis: Lachesis) -> list[int]:
    listed = await lachesis.call_tool("list_open_questions", {})
    assert not listed.is_error, listed.text
    return [question["id"] for question in listed.json()]


@scenario("questions", "Waited-on issue closed")
async def test_waited_on_issue_closed(lachesis: Lachesis) -> None:
    await _repo(lachesis, 161)
    _waited_on(lachesis, 301)
    await _ask_waiting(lachesis)
    _waited_on(lachesis, 301, "closed")

    assert await _open_ids(lachesis) == []

    assert lachesis.github.requests_to("DELETE", f"/repos/{REPO}/issues/161/labels/needs-author")
    answered = await lachesis.call_tool(
        "search_answered_questions", {"repo": REPO, "text": "export"}
    )
    assert not answered.is_error, answered.text
    [question] = answered.json()
    assert "closed" in question["answer"]
    assert f"{OTHER}#301" in question["answer"]
    assert question["outcome"] == "none"


@scenario("questions", "Waited-on issue still open")
async def test_waited_on_issue_still_open(lachesis: Lachesis) -> None:
    await _repo(lachesis, 161)
    _waited_on(lachesis, 301)
    question_id = await _ask_waiting(lachesis)

    assert await _open_ids(lachesis) == [question_id]


@scenario("questions", "Waiting in the same repository")
async def test_waiting_in_the_same_repository(lachesis: Lachesis) -> None:
    await _repo(lachesis, 161)

    asked = await lachesis.call_tool(
        "ask_question",
        {
            "repo": REPO,
            "question": "Can the export ship before #160 lands?",
            "kind": "clarification",
            "asker": "agent",
            "blocks": [161],
            "waits_on": [{"repo": REPO, "issue": 160}],
        },
    )

    assert asked.is_error
    assert "add_dependency" in asked.text
    assert not lachesis.github.requests_to("POST", f"/repos/{REPO}/issues/161/comments")


@scenario("questions", "Guidance says record a dependency")
async def test_guidance_says_record_a_dependency(lachesis: Lachesis) -> None:
    lachesis.github.on("GET", f"/repos/{REPO}/contents/openspec/specs", json=[])
    result = await lachesis.call_tool("get_initial_instructions", {"repo": REPO})
    assert not result.is_error, result.text
    text = " ".join(result.json().lower().split())
    sentences = re.split(r"(?<=[.:;])\s", text)

    assert any("add_dependency" in s and "same repository" in s for s in sentences)
