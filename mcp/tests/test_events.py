"""Tests for mcp/tools/events.py — following the event log."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from auth import current_db_user_id
from tools.events import list_events


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


def _event(event_id: int, kind: str = "note_added") -> dict:
    return {"id": event_id, "repo_name": "owner/repo", "kind": kind, "subject": f"s{event_id}"}


async def test_catching_up_returns_events_oldest_first_with_a_new_cursor(
    set_user_id: str,
) -> None:
    events = [_event(4), _event(5), _event(6)]
    with patch("tools.events.db.list_events", return_value=events) as mock:
        result = await list_events(after=3)

    mock.assert_called_once_with("test-uid", 3, 100, repo_name=None, kind=None)
    assert result == {"events": events, "cursor": 6}


async def test_reading_after_the_newest_cursor_returns_none_and_keeps_it(
    set_user_id: str,
) -> None:
    with patch("tools.events.db.list_events", return_value=[]):
        result = await list_events(after=6)

    assert result == {"events": [], "cursor": 6}


async def test_filters_by_normalized_repository_and_kind(set_user_id: str) -> None:
    with patch("tools.events.db.list_events", return_value=[]) as mock:
        await list_events(repo="https://github.com/owner/repo", kind="question_answered")

    mock.assert_called_once_with(
        "test-uid", 0, 100, repo_name="owner/repo", kind="question_answered"
    )


@pytest.mark.parametrize("limit", [0, 501])
async def test_rejects_a_limit_out_of_range(set_user_id: str, limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be between 1 and 500"):
        await list_events(limit=limit)


async def test_rejects_a_negative_cursor(set_user_id: str) -> None:
    with pytest.raises(ValueError, match="after must be a cursor"):
        await list_events(after=-1)


async def test_raises_when_no_user() -> None:
    token = current_db_user_id.set(None)
    try:
        with pytest.raises(RuntimeError, match="No DB user ID in request context"):
            await list_events()
    finally:
        current_db_user_id.reset(token)
