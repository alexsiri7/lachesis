"""The author's comment on a blocked issue answers the question (spec change #382).

The contract these tests set for the implementation issues:

- The author's GitHub account is the one ``GET /user`` returns for the server's token.
- Every comment Lachesis posts carries the hidden marker ``<!-- lachesis:automated -->``,
  and a comment carrying it never answers a question. Since Lachesis posts with the
  author's token, its own comments come from the author's account too.
- Listing the open questions reads the comments on each issue an open question blocks.
  The author's first unmarked comment made after the question was asked is recorded as
  its answer, answerer ``author``, outcome ``none``; the issues are released as
  ``answer_question`` releases them, and the answer is not posted again.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
ISSUE = 160
AUTHOR = "the-author"
MARKER = "<!-- lachesis:automated -->"
QUESTION = "Is this a duplicate of #12?"
ANSWER = "Close it, it's a duplicate"


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


def _comment(comment_id: int, body: str, created_at: str, login: str = AUTHOR) -> dict[str, Any]:
    return {
        "id": comment_id,
        "body": body,
        "user": {"login": login},
        "created_at": created_at,
        "updated_at": created_at,
        "html_url": f"https://github.com/{REPO}/issues/{ISSUE}#issuecomment-{comment_id}",
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


async def _repo(lachesis: Lachesis) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    github = lachesis.github
    github.on("GET", "/user", json={"login": AUTHOR})
    github.on("GET", f"/repos/{REPO}/issues", json=[_issue(ISSUE)])
    github.on("GET", f"/repos/{REPO}/issues/{ISSUE}", json=_issue(ISSUE))
    github.on("GET", f"/repos/{REPO}/issues/{ISSUE}/comments", json=[])
    github.on(
        "POST",
        f"/repos/{REPO}/issues/{ISSUE}/comments",
        json={"id": 1, "html_url": f"https://github.com/{REPO}/issues/{ISSUE}#issuecomment-1"},
        status=201,
    )
    github.on("POST", f"/repos/{REPO}/labels", json={"name": "needs-author"}, status=201)
    github.on("POST", f"/repos/{REPO}/issues/{ISSUE}/labels", json=[{"name": "needs-author"}])
    github.on("DELETE", f"/repos/{REPO}/issues/{ISSUE}/labels/needs-author", json=[])


async def _ask(lachesis: Lachesis) -> int:
    asked = await lachesis.call_tool(
        "ask_question",
        {
            "repo": REPO,
            "question": QUESTION,
            "kind": "clarification",
            "asker": "agent",
            "blocks": [ISSUE],
        },
    )
    assert not asked.is_error, asked.text
    return int(asked.json()["id"])


def _posted(lachesis: Lachesis) -> list[str]:
    return [
        (request.body or {}).get("body", "")
        for request in lachesis.github.requests_to("POST", f"/repos/{REPO}/issues/{ISSUE}/comments")
    ]


async def _open_ids(lachesis: Lachesis) -> list[int]:
    listed = await lachesis.call_tool("list_open_questions", {})
    assert not listed.is_error, listed.text
    return [question["id"] for question in listed.json()]


@pytest.mark.xfail(strict=True, reason="spec change #382")
@scenario("questions", "Answered on GitHub")
async def test_answered_on_github(lachesis: Lachesis) -> None:
    await _repo(lachesis)
    await _ask(lachesis)
    [question_comment] = _posted(lachesis)
    await asyncio.sleep(2)
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/issues/{ISSUE}/comments",
        json=[_comment(1, question_comment, _now()), _comment(2, ANSWER, _now())],
    )

    assert await _open_ids(lachesis) == []

    answered = await lachesis.call_tool(
        "search_answered_questions", {"repo": REPO, "text": "duplicate"}
    )
    assert not answered.is_error, answered.text
    [question] = answered.json()
    assert question["answer"] == ANSWER
    assert question["outcome"] == "none"
    assert question["answerer"] == "author"
    assert lachesis.github.requests_to(
        "DELETE", f"/repos/{REPO}/issues/{ISSUE}/labels/needs-author"
    )
    assert _posted(lachesis) == [question_comment]


@pytest.mark.xfail(strict=True, reason="spec change #382")
@scenario("questions", "Automated comment is not an answer")
async def test_automated_comment_is_not_an_answer(lachesis: Lachesis) -> None:
    await _repo(lachesis)
    question_id = await _ask(lachesis)
    [question_comment] = _posted(lachesis)
    await asyncio.sleep(2)
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/issues/{ISSUE}/comments",
        json=[
            _comment(1, question_comment, _now()),
            _comment(2, f"{MARKER}\n{ANSWER}", _now()),
        ],
    )

    assert MARKER in question_comment
    assert await _open_ids(lachesis) == [question_id]
    assert not lachesis.github.requests_to(
        "DELETE", f"/repos/{REPO}/issues/{ISSUE}/labels/needs-author"
    )


@scenario("questions", "Comment from before the question")
async def test_comment_from_before_the_question(lachesis: Lachesis) -> None:
    await _repo(lachesis)
    lachesis.github.on(
        "GET",
        f"/repos/{REPO}/issues/{ISSUE}/comments",
        json=[_comment(1, ANSWER, "2026-01-01T00:00:00Z")],
    )
    question_id = await _ask(lachesis)

    assert await _open_ids(lachesis) == [question_id]
    assert not lachesis.github.requests_to(
        "DELETE", f"/repos/{REPO}/issues/{ISSUE}/labels/needs-author"
    )
