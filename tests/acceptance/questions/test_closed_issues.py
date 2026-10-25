"""A question settles when every issue it blocks closes (issue #402).

The contract these tests set for the implementation:

- Reading the open questions (``list_open_questions``, ``next_issue``, the dashboard and the
  web view) reads each issue an open question blocks. When every one is closed, an answer
  naming them as closed is recorded with outcome ``none`` and the question leaves the queue.
- A question that blocks no issue, or still blocks an open one, stays open.
"""

from __future__ import annotations

from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"


def _issue(number: int, state: str = "open") -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": state,
        "labels": [{"name": "needs-author"}],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None if state == "open" else "2026-01-02T00:00:00+00:00",
        "state_reason": None if state == "open" else "completed",
    }


async def _repo(lachesis: Lachesis, *numbers: int) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    github = lachesis.github
    github.on("GET", "/user", json={"login": "the-author"})
    github.on("GET", f"/repos/{REPO}/issues", json=[_issue(number) for number in numbers])
    github.on("POST", f"/repos/{REPO}/labels", json={"name": "needs-author"}, status=201)
    for number in numbers:
        github.on("GET", f"/repos/{REPO}/issues/{number}", json=_issue(number))
        github.on("GET", f"/repos/{REPO}/issues/{number}/comments", json=[])
        github.on(
            "POST",
            f"/repos/{REPO}/issues/{number}/comments",
            json={"id": number, "html_url": f"https://github.com/{REPO}/issues/{number}#c"},
            status=201,
        )
        github.on(
            "POST", f"/repos/{REPO}/issues/{number}/labels", json=[{"name": "needs-author"}]
        )
        github.on("DELETE", f"/repos/{REPO}/issues/{number}/labels/needs-author", json=[])


def _close(lachesis: Lachesis, number: int) -> None:
    lachesis.github.on("GET", f"/repos/{REPO}/issues/{number}", json=_issue(number, "closed"))


async def _ask(lachesis: Lachesis, question: str, **subject: Any) -> int:
    asked = await lachesis.call_tool(
        "ask_question",
        {"repo": REPO, "question": question, "kind": "clarification", "asker": "agent", **subject},
    )
    assert not asked.is_error, asked.text
    return int(asked.json()["id"])


async def _open_ids(lachesis: Lachesis) -> list[int]:
    listed = await lachesis.call_tool("list_open_questions", {})
    assert not listed.is_error, listed.text
    return [question["id"] for question in listed.json()]


@scenario("questions", "Issue closed before the answer")
async def test_issue_closed_before_the_answer(lachesis: Lachesis) -> None:
    await _repo(lachesis, 160)
    question_id = await _ask(lachesis, "Should the export be CSV or JSON?", issue=160)
    _close(lachesis, 160)

    listed = await lachesis.call_tool("list_open_questions", {})

    assert not listed.is_error, listed.text
    [question] = listed.json()
    assert question["id"] == question_id
    assert question["question"] == "Should the export be CSV or JSON?"


@scenario("questions", "Every blocked issue closed")
async def test_every_blocked_issue_closed(lachesis: Lachesis) -> None:
    await _repo(lachesis, 160, 161)
    await _ask(lachesis, "Should the export be CSV or JSON?", blocks=[160, 161])
    _close(lachesis, 160)
    _close(lachesis, 161)

    assert await _open_ids(lachesis) == []

    answered = await lachesis.call_tool(
        "search_answered_questions", {"repo": REPO, "text": "export"}
    )
    assert not answered.is_error, answered.text
    [question] = answered.json()
    assert "closed" in question["answer"]
    assert "#160" in question["answer"] and "#161" in question["answer"]
    assert question["outcome"] == "none"


@scenario("questions", "One blocked issue still open")
async def test_one_blocked_issue_still_open(lachesis: Lachesis) -> None:
    await _repo(lachesis, 160, 161)
    question_id = await _ask(lachesis, "Should the export be CSV or JSON?", blocks=[160, 161])
    _close(lachesis, 160)

    assert await _open_ids(lachesis) == [question_id]
