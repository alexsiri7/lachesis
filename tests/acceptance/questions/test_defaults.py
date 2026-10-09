"""Small questions settle by default while the author is away.

The contract these tests set for the implementation issues of spec change #319:

- ``ask_question(..., default: str | None = None, wait_days: float = 3)`` and
  ``ask_author(repo, number, question, asker="agent", default=None, wait_days=3)``. A
  ``default`` is accepted only for kinds ``clarification`` and ``improvement``; offering one
  for any other kind is refused and nothing is asked. ``wait_days`` must be positive.
- Reading the open questions (``list_open_questions``, ``next_issue``, the dashboard) settles
  each question whose wait has passed unanswered: its default is recorded as the answer with
  outcome ``defaulted`` (answerer ``default``), the issues it blocked are released as an answer
  would release them, and each gets a comment saying the default was taken.
- ``search_answered_questions`` returns a defaulted question with its answer and outcome.

The harness has no clock, so "asked four days ago" with the usual three-day wait is a
question asked with a wait of one second, read two seconds later.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
ISSUE = 160
ONE_SECOND_IN_DAYS = 1 / 86400


def _issue(number: int) -> dict[str, Any]:
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "open",
        "labels": [{"name": "needs-author"}],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "closed_at": None,
        "state_reason": None,
    }


async def _repo(lachesis: Lachesis) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    github = lachesis.github
    github.on("GET", f"/repos/{REPO}/issues", json=[_issue(ISSUE)])
    github.on("GET", f"/repos/{REPO}/issues/{ISSUE}", json=_issue(ISSUE))
    github.on(
        "POST",
        f"/repos/{REPO}/issues/{ISSUE}/comments",
        json={"id": 1, "html_url": f"https://github.com/{REPO}/issues/{ISSUE}#issuecomment-1"},
        status=201,
    )
    github.on("GET", f"/repos/{REPO}/labels/needs-author", json={"name": "needs-author"})
    github.on("POST", f"/repos/{REPO}/labels", json={"name": "needs-author"}, status=201)
    github.on("POST", f"/repos/{REPO}/issues/{ISSUE}/labels", json=[{"name": "needs-author"}])
    github.on("DELETE", f"/repos/{REPO}/issues/{ISSUE}/labels/needs-author", json=[])


def _comments_on(lachesis: Lachesis, number: int) -> list[str]:
    return [
        (request.body or {}).get("body", "")
        for request in lachesis.github.requests_to(
            "POST", f"/repos/{REPO}/issues/{number}/comments"
        )
    ]


@pytest.mark.xfail(strict=True, reason="spec change #319")
@scenario("questions", "Away for a week")
async def test_away_for_a_week(lachesis: Lachesis) -> None:
    await _repo(lachesis)
    asked = await lachesis.call_tool(
        "ask_question",
        {
            "repo": REPO,
            "question": "Should the export be CSV or JSON?",
            "kind": "clarification",
            "asker": "agent",
            "blocks": [ISSUE],
            "default": "CSV",
            "wait_days": ONE_SECOND_IN_DAYS,
        },
    )
    assert not asked.is_error, asked.text
    await asyncio.sleep(2)

    still_open = await lachesis.call_tool("list_open_questions", {})

    assert not still_open.is_error, still_open.text
    assert still_open.json() == []
    answered = await lachesis.call_tool(
        "search_answered_questions", {"repo": REPO, "text": "export"}
    )
    assert not answered.is_error, answered.text
    [question] = answered.json()
    assert question["answer"] == "CSV"
    assert question["outcome"] == "defaulted"
    assert lachesis.github.requests_to(
        "DELETE", f"/repos/{REPO}/issues/{ISSUE}/labels/needs-author"
    )
    notes = _comments_on(lachesis, ISSUE)[1:]
    assert any("default" in note.lower() and "CSV" in note for note in notes)


@pytest.mark.xfail(strict=True, reason="spec change #319")
@scenario("questions", "Spec decision waits")
async def test_spec_decision_waits(lachesis: Lachesis) -> None:
    await _repo(lachesis)
    question = {
        "repo": REPO,
        "question": "The backlog spec says oldest first; the sprint spec says newest. Which?",
        "kind": "spec_contradiction",
        "asker": "agent",
        "blocks": [ISSUE],
    }

    offered = await lachesis.call_tool(
        "ask_question", {**question, "default": "Oldest first", "wait_days": ONE_SECOND_IN_DAYS}
    )
    asked = await lachesis.call_tool("ask_question", question)
    await asyncio.sleep(2)
    waiting = await lachesis.call_tool("list_open_questions", {})

    assert offered.is_error
    assert "default" in offered.text.lower()
    assert "unexpected keyword" not in offered.text.lower()
    assert not asked.is_error, asked.text
    assert not waiting.is_error, waiting.text
    [open_question] = waiting.json()
    assert open_question["kind"] == "spec_contradiction"
    assert not lachesis.github.requests_to(
        "DELETE", f"/repos/{REPO}/issues/{ISSUE}/labels/needs-author"
    )
