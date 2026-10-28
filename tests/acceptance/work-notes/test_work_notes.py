"""Work notes: on exactly one issue or milestone, append-only, offered with their work."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, scenario

REPO = "owner/repo"
ISSUE = 160


def _issue(number: int, *, labels: tuple[str, ...] = ()) -> dict[str, Any]:
    created = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    return {
        "number": number,
        "id": 1000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "open",
        "labels": [{"name": label} for label in labels],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": created,
        "updated_at": created,
        "closed_at": None,
        "state_reason": None,
    }


async def _repo_with(lachesis: Lachesis, *items: dict[str, Any]) -> None:
    registered = await lachesis.call_tool("register_repo", {"repo": REPO})
    assert not registered.is_error, registered.text
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=list(items))
    for item in items:
        lachesis.github.on("GET", f"/repos/{REPO}/issues/{item['number']}", json=item)


async def _add_note(lachesis: Lachesis, text: str, **subject: Any) -> dict[str, Any]:
    added = await lachesis.call_tool(
        "add_note",
        {"repo": REPO, "role": "implementer", "type": "handoff", "text": text, **subject},
    )
    assert not added.is_error, added.text
    return added.json()


async def _notes_on_issue(lachesis: Lachesis) -> list[dict[str, Any]]:
    listed = await lachesis.call_tool("list_notes", {"repo": REPO, "issue": ISSUE})
    assert not listed.is_error, listed.text
    return listed.json()


@scenario("work-notes", "Handoff note")
async def test_handoff_note(lachesis: Lachesis) -> None:
    await _repo_with(lachesis, _issue(ISSUE, labels=("archon:in-progress",)))
    before = datetime.now(UTC) - timedelta(seconds=1)

    note = await _add_note(lachesis, "Parser done; wiring the tool next.", issue=ISSUE)

    [stored] = await _notes_on_issue(lachesis)
    assert stored == note
    assert stored["issue"] == ISSUE
    assert stored["milestone"] is None
    assert stored["role"] == "implementer"
    assert stored["type"] == "handoff"
    assert stored["text"] == "Parser done; wiring the tool next."
    assert datetime.fromisoformat(stored["created_at"]) >= before


@scenario("work-notes", "Nothing to attach to")
async def test_nothing_to_attach_to(lachesis: Lachesis) -> None:
    await _repo_with(lachesis)

    added = await lachesis.call_tool(
        "add_note",
        {"repo": REPO, "role": "implementer", "type": "handoff", "text": "Where does this go?"},
    )

    assert added.is_error


@scenario("work-notes", "Editing refused")
async def test_editing_refused(lachesis: Lachesis) -> None:
    await _repo_with(lachesis, _issue(ISSUE))
    note = await _add_note(lachesis, "Original text.", issue=ISSUE)

    tools = await lachesis.list_tools()
    edited = await lachesis.call_tool("update_note", {"id": note["id"], "text": "Changed."})

    assert sorted(name for name in tools if "note" in name) == ["add_note", "list_notes"]
    assert "id" not in tools["add_note"].get("properties", {})
    assert edited.is_error
    assert await _notes_on_issue(lachesis) == [note]


@scenario("work-notes", "Picking up after a crash")
async def test_picking_up_after_a_crash(lachesis: Lachesis) -> None:
    await _repo_with(lachesis, _issue(ISSUE))
    first = await _add_note(lachesis, "Started on the parser.", issue=ISSUE)
    second = await _add_note(lachesis, "Parser done; tests next.", issue=ISSUE)

    offered = await lachesis.call_tool("next_issue", {"repo": REPO})

    assert not offered.is_error, offered.text
    result = offered.json()
    assert result["issue"]["number"] == ISSUE
    assert result["notes"] == [first, second]
