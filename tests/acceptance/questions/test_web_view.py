"""Each open question in full on the web view, with a link to discuss it in Claude.

The contract these tests set for the implementation issues of spec change #382:

- The web view lists the open questions in ``<section id="questions">``. Each question's
  element carries ``data-question`` (its id) and ``data-waited`` (how long it has waited),
  and the section shows its question, context and every option.
- Each issue a question blocks is an ``<a data-blocks="N">`` linking to the issue on GitHub.
- Each question has an ``<a data-discuss="ID">`` linking to ``https://claude.ai/new`` with
  the pre-filled message in its ``q`` query parameter: the question's id, repository,
  question, context, every option, every blocked issue's number and ``answer_question``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from harness import Lachesis, elements_in, scenario, section_of, with_attribute

REPO = "owner/repo"
ISSUE = 160
QUESTION = "Which format should the export use?"
CONTEXT = "The importer on the other side reads both formats."
OPTIONS = ["CSV", "JSON"]


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


async def _open_question(lachesis: Lachesis) -> int:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    github = lachesis.github
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
    asked = await lachesis.call_tool(
        "ask_question",
        {
            "repo": REPO,
            "question": QUESTION,
            "kind": "clarification",
            "asker": "agent",
            "blocks": [ISSUE],
            "context": CONTEXT,
            "options": OPTIONS,
        },
    )
    assert not asked.is_error, asked.text
    return int(asked.json()["id"])


@pytest.mark.xfail(strict=True, reason="spec change #382")
@scenario("questions", "Question on the page")
async def test_question_on_the_page(lachesis: Lachesis) -> None:
    question_id = await _open_question(lachesis)

    section = section_of(await lachesis.web_page(), "questions")

    elements = elements_in(section)
    [shown] = [
        e
        for e in with_attribute(elements, "data-question")
        if e["data-question"] == str(question_id)
    ]
    assert shown["data-waited"]
    assert QUESTION in section
    assert CONTEXT in section
    for option in OPTIONS:
        assert option in section
    [blocked] = with_attribute(elements, "data-blocks")
    assert blocked["data-blocks"] == str(ISSUE)
    assert blocked["href"] == f"https://github.com/{REPO}/issues/{ISSUE}"


@pytest.mark.xfail(strict=True, reason="spec change #382")
@scenario("questions", "Discuss in Claude")
async def test_discuss_in_claude(lachesis: Lachesis) -> None:
    question_id = await _open_question(lachesis)

    section = section_of(await lachesis.web_page(), "questions")

    [discuss] = [
        e
        for e in with_attribute(elements_in(section), "data-discuss")
        if e["data-discuss"] == str(question_id)
    ]
    link = urlsplit(discuss["href"])
    assert (link.scheme, link.netloc, link.path) == ("https", "claude.ai", "/new")
    [message] = parse_qs(link.query)["q"]
    assert str(question_id) in message
    assert REPO in message
    assert QUESTION in message
    assert CONTEXT in message
    for option in OPTIONS:
        assert option in message
    assert str(ISSUE) in message
    assert "answer_question" in message
