"""Tests for mcp/tools/notes.py — work notes on issues and milestones."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from auth import current_db_user_id
from tools.notes import add_note, list_notes

_REGISTERED = [{"id": "r1", "repo_name": "alexsiri7/lachesis", "created_at": "t"}]


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def registered():
    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        yield


async def test_handoff_note_is_stored_on_the_issue(set_user_id: str, registered: None) -> None:
    """Scenario: Handoff note — stored on issue 160 with role, type and text."""
    stored = {"id": 4, "issue": 160, "role": "implementer", "type": "handoff"}
    with patch("tools.notes.db.record_note", return_value=stored) as record:
        result = await add_note(
            "lachesis", " implementer ", "handoff", " Tests pass; docs left. ", issue=160
        )

    assert result == stored
    record.assert_called_once_with(
        "test-uid",
        "alexsiri7/lachesis",
        "implementer",
        "handoff",
        "Tests pass; docs left.",
        issue=160,
        milestone=None,
    )


async def test_a_note_can_attach_to_a_milestone(set_user_id: str, registered: None) -> None:
    with patch("tools.notes.db.record_note", return_value={"id": 5}) as record:
        await add_note("alexsiri7/lachesis", "planner", "decision", "Ship A.", milestone="m1")

    assert record.call_args.kwargs == {"issue": None, "milestone": "m1"}


@pytest.mark.parametrize("subject", [{}, {"issue": 160, "milestone": "m1"}])
async def test_a_note_must_name_exactly_one_issue_or_milestone(
    set_user_id: str, registered: None, subject: dict
) -> None:
    """Scenario: Nothing to attach to — refused before anything is stored."""
    with (
        patch("tools.notes.db.record_note") as record,
        pytest.raises(ValueError, match="exactly one of issue or milestone"),
    ):
        await add_note("lachesis", "implementer", "handoff", "Half done.", **subject)

    record.assert_not_called()


@pytest.mark.parametrize(
    ("role", "note_type", "text", "match"),
    [
        ("", "handoff", "x", "role"),
        ("implementer", "handoff", "  ", "text"),
        ("implementer", "handof", "x", "type must be one of"),
    ],
)
async def test_refuses_an_incomplete_note(
    set_user_id: str, registered: None, role: str, note_type: str, text: str, match: str
) -> None:
    with (
        patch("tools.notes.db.record_note") as record,
        pytest.raises(ValueError, match=match),
    ):
        await add_note("lachesis", role, note_type, text, issue=160)  # type: ignore[arg-type]

    record.assert_not_called()


async def test_refuses_an_unregistered_repository(set_user_id: str, registered: None) -> None:
    with (
        patch("tools.notes.db.record_note") as record,
        pytest.raises(ValueError, match="not registered"),
    ):
        await add_note("owner/other", "implementer", "handoff", "x", issue=1)

    record.assert_not_called()


async def test_list_notes_reads_the_issues_notes(set_user_id: str, registered: None) -> None:
    notes = [{"id": 1}, {"id": 4}]
    with patch("tools.notes.db.list_notes", return_value=notes) as read:
        result = await list_notes("lachesis", issue=160)

    assert result == notes
    read.assert_called_once_with("test-uid", "alexsiri7/lachesis", issue=160, milestone=None)


async def test_list_notes_needs_exactly_one_subject(set_user_id: str) -> None:
    with pytest.raises(ValueError, match="exactly one of issue or milestone"):
        await list_notes("lachesis")
