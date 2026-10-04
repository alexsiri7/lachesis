"""Tests for mcp/tools/focus.py — the author's focus on repositories and milestones."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from auth import current_db_user_id
from tools.focus import clear_focus, set_focus

_REPO = "alexsiri7/lachesis"
_REGISTERED = [{"id": "r1", "repo_name": _REPO, "created_at": "t"}]


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def registered():
    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        yield


class _FakeFocus:
    """In-memory stand-in for the lachesis_focus accessors in db.py."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add_focus(self, user_id: str, repo_name: str, milestone_id: int | None) -> None:
        row = {"repo_name": repo_name, "milestone_id": milestone_id}
        if row not in self.rows:
            self.rows.append(row)

    def remove_focus(self, user_id: str, repo_name: str, milestone_id: int | None) -> None:
        self.rows.remove({"repo_name": repo_name, "milestone_id": milestone_id})

    def list_focus(self, user_id: str) -> list[dict]:
        return list(self.rows)


@pytest.fixture()
def focus():
    fake = _FakeFocus()
    with (
        patch("tools.focus.db.add_focus", fake.add_focus),
        patch("tools.focus.db.remove_focus", fake.remove_focus),
        patch("tools.focus.db.list_focus", fake.list_focus),
        patch("tools.focus.db.get_milestone", return_value={"id": 2, "dropped_at": None}),
    ):
        yield fake


async def test_set_focus_returns_every_focus(
    set_user_id: str, registered: None, focus: _FakeFocus
) -> None:
    await set_focus("lachesis")
    result = await set_focus("lachesis", milestone=2)

    assert result == [{"repo": _REPO, "milestone": None}, {"repo": _REPO, "milestone": 2}]


async def test_clear_focus_on_a_repository_leaves_its_milestones_focused(
    set_user_id: str, registered: None, focus: _FakeFocus
) -> None:
    await set_focus("lachesis")
    await set_focus("lachesis", milestone=2)

    result = await clear_focus("lachesis")

    assert result == [{"repo": _REPO, "milestone": 2}]


async def test_set_focus_refuses_a_milestone_not_in_the_repository(
    set_user_id: str, registered: None, focus: _FakeFocus
) -> None:
    with (
        patch("tools.focus.db.get_milestone", return_value=None),
        pytest.raises(ValueError, match="not found"),
    ):
        await set_focus("lachesis", milestone=9)

    assert focus.rows == []


async def test_set_focus_refuses_a_dropped_milestone(
    set_user_id: str, registered: None, focus: _FakeFocus
) -> None:
    dropped = {"id": 2, "dropped_at": datetime(2026, 10, 1, tzinfo=UTC)}
    with (
        patch("tools.focus.db.get_milestone", return_value=dropped),
        pytest.raises(ValueError, match="dropped"),
    ):
        await set_focus("lachesis", milestone=2)

    assert focus.rows == []


async def test_set_focus_refuses_an_unregistered_repository(
    set_user_id: str, registered: None, focus: _FakeFocus
) -> None:
    with pytest.raises(ValueError):
        await set_focus("someone/else")

    assert focus.rows == []
