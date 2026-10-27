"""Changes leave events, and the event log can be followed from a cursor."""

from __future__ import annotations

from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
ISSUE = 7


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


async def _register(lachesis: Lachesis) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text


async def _events_after(lachesis: Lachesis, cursor: int, **filters: Any) -> dict[str, Any]:
    listed = await lachesis.call_tool("list_events", {"after": cursor, **filters})
    assert not listed.is_error, listed.text
    return listed.json()


@scenario("events", "Answer recorded")
async def test_answer_recorded(lachesis: Lachesis) -> None:
    await _register(lachesis)
    github = lachesis.github
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
    cursor = (await _events_after(lachesis, 0))["cursor"]
    asked = await lachesis.call_tool(
        "ask_question",
        {
            "repo": REPO,
            "question": "Should the export include archived items?",
            "kind": "clarification",
            "asker": "agent",
            "blocks": [ISSUE],
        },
    )
    assert not asked.is_error, asked.text
    question_id = int(asked.json()["id"])

    answered = await lachesis.call_tool(
        "answer_question",
        {"repo": REPO, "id": question_id, "answer": "No, leave them out", "answerer": "author"},
    )
    assert not answered.is_error, answered.text

    events = (await _events_after(lachesis, cursor, kind="question_answered"))["events"]
    assert [(event["subject"], event["repo_name"]) for event in events] == [
        (str(question_id), REPO)
    ]


@scenario("events", "Catching up")
async def test_catching_up(lachesis: Lachesis) -> None:
    await _register(lachesis)
    cursor = (await _events_after(lachesis, 0))["cursor"]
    note_ids = []
    for text in ("first", "second", "third"):
        added = await lachesis.call_tool(
            "add_note",
            {"repo": REPO, "role": "implementer", "type": "progress", "text": text, "issue": ISSUE},
        )
        assert not added.is_error, added.text
        note_ids.append(str(added.json()["id"]))

    caught_up = await _events_after(lachesis, cursor)

    assert [event["subject"] for event in caught_up["events"]] == note_ids
    assert caught_up["cursor"] != cursor
    assert await _events_after(lachesis, caught_up["cursor"]) == {
        "events": [],
        "cursor": caught_up["cursor"],
    }
