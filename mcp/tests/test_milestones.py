"""Tests for mcp/tools/milestones.py — milestones, whose state is read from the work."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import landing
import main
from auth import current_db_user_id
from tools import milestones
from tools.specs import SpecProgress

_REPO = "alexsiri7/lachesis"
_REGISTERED = [{"id": "r1", "repo_name": _REPO, "created_at": "t"}]


@pytest.fixture(autouse=True)
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture(autouse=True)
def registered():
    with patch("tools.requirements.db.list_repos", return_value=_REGISTERED):
        yield


@pytest.fixture()
def record_state():
    with patch("tools.milestones.db.record_milestone_state", return_value=True) as record:
        yield record


@pytest.fixture()
def deployment():
    with patch("tools.milestones.github.get_production_deployment", new=AsyncMock()) as get:
        get.return_value = {"state": "success", "url": "https://deploy/1"}
        yield get


def _row(
    milestone_id: int = 7,
    spec_changes: list[int] | None = None,
    *,
    started: bool = True,
    dropped: bool = False,
) -> dict[str, Any]:
    return {
        "id": milestone_id,
        "repo_name": _REPO,
        "title": "Milestones",
        "goal": "Group spec changes",
        "created_at": "2026-10-01T00:00:00Z",
        "started_at": "2026-10-02T00:00:00Z" if started else None,
        "dropped_at": "2026-10-03T00:00:00Z" if dropped else None,
        "spec_changes": [170] if spec_changes is None else spec_changes,
    }


def _entry(
    number: int,
    *,
    state: str = "merged",
    status: str | None = "done",
    source: str = "issues",
    closed_without_merge: bool = False,
    landed_at: str | None = "2026-10-02T00:00:00Z",
    landed_commit: str | None = "abc",
    dropped: bool = False,
) -> dict[str, Any]:
    is_done = status == "done"
    entry = {
        "spec_change": number,
        "title": f"Spec change {number}",
        "url": f"https://github.com/{_REPO}/pull/{number}",
        "state": state,
        "dropped": dropped,
        "derived": {
            "status": status,
            "source": source,
            "landed_at": landed_at if is_done else None,
            "landed_commit": landed_commit if is_done else None,
            "signals": {"closed_without_merge": closed_without_merge},
        },
    }
    if dropped:
        entry["reason"] = "its pull request closed without merging"
    return entry


def _progress(*entries: dict[str, Any]):
    return patch(
        "tools.milestones.read_spec_progress",
        new=AsyncMock(
            return_value=SpecProgress({}, [landing.SpecChangeCandidate("", e) for e in entries])
        ),
    )


# ---------------------------------------------------------------------------
# Derived state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec_changes", "entries", "state", "conclusive"),
    [
        ([], [], "active", True),
        ([170], [_entry(170, status="in-progress")], "active", True),
        ([170], [_entry(170, state="open", status="in-progress")], "active", True),
        ([170], [_entry(170, status=None, source="unlinked")], "active", True),
        ([170], [_entry(170, status=None, source="unavailable")], "active", False),
        ([170, 172], [_entry(170)], "active", False),
        (
            [170, 172],
            [_entry(170), _entry(172, status="in-progress", closed_without_merge=True)],
            "verifying",
            True,
        ),
        ([170], [_entry(170, landed_commit=None)], "verifying", True),
    ],
)
async def test_state_before_anything_is_checked_against_production(
    deployment: AsyncMock,
    spec_changes: list[int],
    entries: list[dict[str, Any]],
    state: str,
    conclusive: bool,
) -> None:
    derivation = await milestones.derive_milestone_state(
        _REPO, spec_changes, {e["spec_change"]: e for e in entries}
    )

    assert (derivation.state, derivation.conclusive) == (state, conclusive)
    deployment.assert_not_called()


@pytest.mark.parametrize("verdict", [None, {"state": "pending"}, {"state": "failure"}])
async def test_merged_but_not_yet_live(deployment: AsyncMock, verdict: dict | None) -> None:
    """Scenario: Merged but not yet live — verifying until production includes the merge."""
    deployment.return_value = verdict

    derivation = await milestones.derive_milestone_state(_REPO, [170], {170: _entry(170)})

    assert derivation.state == "verifying"
    assert derivation.conclusive


async def test_live_once_production_includes_the_last_merge(deployment: AsyncMock) -> None:
    """Scenario: Live — every spec change landed and production includes the last merge."""
    entries = {
        170: _entry(170, landed_at="2026-10-02T00:00:00Z", landed_commit="early"),
        172: _entry(172, landed_at="2026-10-03T00:00:00Z", landed_commit="late"),
    }

    derivation = await milestones.derive_milestone_state(_REPO, [170, 172], entries)

    assert derivation.state == "done"
    assert derivation.last_merge == "late"
    deployment.assert_awaited_once_with(_REPO, "late")


async def test_planned_and_dropped_milestones_read_nothing_from_github(
    deployment: AsyncMock, record_state
) -> None:
    rows = [_row(1, started=False), _row(2, dropped=True)]
    with (
        patch("tools.milestones.db.list_milestones", return_value=rows),
        _progress() as progress,
    ):
        views = await milestones.list_milestones("lachesis")

    assert [v["state"] for v in views] == ["planned", "dropped"]
    progress.assert_not_called()
    record_state.assert_not_called()


async def test_list_reads_spec_progress_once_for_every_started_milestone(
    deployment: AsyncMock, record_state
) -> None:
    rows = [_row(1, [170]), _row(2, [172])]
    with (
        patch("tools.milestones.db.list_milestones", return_value=rows),
        _progress(_entry(170), _entry(172, status="in-progress")) as progress,
    ):
        views = await milestones.list_milestones("lachesis")

    progress.assert_awaited_once_with(_REPO)
    assert [v["state"] for v in views] == ["done", "active"]
    assert views[1]["work"] == [
        {
            "spec_change": 172,
            "title": "Spec change 172",
            "url": f"https://github.com/{_REPO}/pull/172",
            "status": "in-progress",
        }
    ]


async def test_a_conclusive_read_records_the_state(deployment: AsyncMock, record_state) -> None:
    deployment.return_value = None
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row()),
        _progress(_entry(170)),
    ):
        view = await milestones.get_milestone("lachesis", 7)

    assert view["state"] == "verifying"
    record_state.assert_called_once_with("test-uid", _REPO, 7, "verifying")


async def test_an_inconclusive_read_records_nothing(deployment: AsyncMock, record_state) -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row(spec_changes=[170])),
        _progress(_entry(170, status=None, source="unavailable")),
    ):
        view = await milestones.get_milestone("lachesis", 7)

    assert view["state"] == "active"
    assert view["work"][0]["status"] == "unreadable"
    record_state.assert_not_called()


async def test_failing_to_record_the_state_does_not_fail_the_read(deployment: AsyncMock) -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row()),
        patch("tools.milestones.db.record_milestone_state", side_effect=RuntimeError("db down")),
        _progress(_entry(170)),
    ):
        view = await milestones.get_milestone("lachesis", 7)

    assert view["state"] == "done"


async def test_an_unknown_milestone_is_not_found() -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=None),
        pytest.raises(ValueError, match="Milestone 9 not found"),
    ):
        await milestones.get_milestone("lachesis", 9)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def test_creating_a_milestone(deployment: AsyncMock, record_state) -> None:
    """Scenario: Creating a milestone — planned, with its title, goal and spec changes."""
    with (
        patch("tools.milestones.uses_specs", new=AsyncMock(return_value=True)),
        _progress(_entry(170, status="idea"), _entry(172, status="idea")),
        patch(
            "tools.milestones.db.create_milestone",
            return_value=_row(spec_changes=[170, 172], started=False),
        ) as create,
    ):
        view = await milestones.create_milestone(
            "lachesis", " Milestones ", " Group spec changes ", [172, 170, 172]
        )

    create.assert_called_once_with(
        "test-uid", _REPO, "Milestones", "Group spec changes", [170, 172]
    )
    assert view["title"] == "Milestones"
    assert view["goal"] == "Group spec changes"
    assert view["spec_changes"] == [170, 172]
    assert view["state"] == "planned"
    deployment.assert_not_called()


@pytest.mark.parametrize(
    ("title", "goal", "spec_changes", "match"),
    [
        (" ", "Goal", [], "title"),
        ("Title", "", [], "goal"),
        ("Title", "Goal", [999], "#999 is not a spec change"),
        ("Title", "Goal", [171], "was dropped"),
    ],
)
async def test_create_refuses(title: str, goal: str, spec_changes: list[int], match: str) -> None:
    with (
        patch("tools.milestones.uses_specs", new=AsyncMock(return_value=True)),
        _progress(_entry(170), _entry(171, state="closed", dropped=True)),
        patch("tools.milestones.db.create_milestone") as create,
        pytest.raises(ValueError, match=match),
    ):
        await milestones.create_milestone("lachesis", title, goal, spec_changes)

    create.assert_not_called()


async def test_create_refuses_a_repo_without_specs() -> None:
    with (
        patch("tools.milestones.uses_specs", new=AsyncMock(return_value=False)),
        patch("tools.milestones.db.create_milestone") as create,
        pytest.raises(ValueError, match="does not use specs"),
    ):
        await milestones.create_milestone("lachesis", "Title", "Goal", [170])

    create.assert_not_called()


async def test_create_refuses_an_unregistered_repo() -> None:
    with (
        patch("tools.milestones.db.create_milestone") as create,
        pytest.raises(ValueError),
    ):
        await milestones.create_milestone("someone/else", "Title", "Goal")

    create.assert_not_called()


async def test_already_in_another_milestone_is_refused() -> None:
    """Scenario: Already in another milestone — the refusal names milestone A."""
    refusal = ValueError("#170 is already in milestone 3 (A); a spec change belongs to at most one")
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row(7, [], started=False)),
        _progress(_entry(170)),
        patch("tools.milestones.db.add_milestone_spec_change", side_effect=refusal),
        pytest.raises(ValueError, match=r"milestone 3 \(A\)"),
    ):
        await milestones.add_milestone_spec_change("lachesis", 7, 170)


async def test_start_refusal_propagates() -> None:
    with (
        patch(
            "tools.milestones.db.start_milestone",
            side_effect=ValueError("Milestone 7 is already started"),
        ),
        _progress() as progress,
        pytest.raises(ValueError, match="already started"),
    ):
        await milestones.start_milestone("lachesis", 7)

    progress.assert_not_called()


async def test_a_done_milestone_cannot_be_dropped(deployment: AsyncMock, record_state) -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row()),
        _progress(_entry(170)),
        patch("tools.milestones.db.drop_milestone") as drop,
        pytest.raises(ValueError, match="done; it cannot be dropped"),
    ):
        await milestones.drop_milestone("lachesis", 7)

    drop.assert_not_called()


async def test_an_unfinished_milestone_can_be_dropped(deployment: AsyncMock, record_state) -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row()),
        _progress(_entry(170, status="in-progress")),
        patch("tools.milestones.db.drop_milestone", return_value=_row(dropped=True)) as drop,
    ):
        view = await milestones.drop_milestone("lachesis", 7)

    drop.assert_called_once_with("test-uid", _REPO, 7)
    assert view["state"] == "dropped"


async def test_a_planned_milestone_is_dropped_without_reading_github(
    deployment: AsyncMock,
) -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row(started=False)),
        _progress() as progress,
        patch(
            "tools.milestones.db.drop_milestone", return_value=_row(started=False, dropped=True)
        ) as drop,
    ):
        view = await milestones.drop_milestone("lachesis", 7)

    drop.assert_called_once_with("test-uid", _REPO, 7)
    assert view["state"] == "dropped"
    progress.assert_not_called()
    deployment.assert_not_called()


@pytest.mark.parametrize(("row", "match"), [(_row(), "done"), (_row(dropped=True), "dropped")])
async def test_add_is_refused_when_done_or_dropped(
    deployment: AsyncMock, record_state, row: dict, match: str
) -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=row),
        _progress(_entry(170), _entry(172)),
        patch("tools.milestones.db.add_milestone_spec_change") as add,
        pytest.raises(ValueError, match=match),
    ):
        await milestones.add_milestone_spec_change("lachesis", 7, 172)

    add.assert_not_called()


async def test_remove_is_refused_when_done(deployment: AsyncMock, record_state) -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row()),
        _progress(_entry(170)),
        patch("tools.milestones.db.remove_milestone_spec_change") as remove,
        pytest.raises(ValueError, match="done"),
    ):
        await milestones.remove_milestone_spec_change("lachesis", 7, 170)

    remove.assert_not_called()


async def test_remove_is_allowed_on_a_dropped_milestone(deployment: AsyncMock) -> None:
    with (
        patch("tools.milestones.db.get_milestone", return_value=_row(dropped=True)),
        _progress() as progress,
        patch(
            "tools.milestones.db.remove_milestone_spec_change",
            return_value=_row(spec_changes=[], dropped=True),
        ) as remove,
    ):
        view = await milestones.remove_milestone_spec_change("lachesis", 7, 170)

    remove.assert_called_once_with("test-uid", _REPO, 7, 170)
    assert view["spec_changes"] == []
    progress.assert_not_called()


async def test_milestone_tools_are_registered() -> None:
    tools = {tool.name: tool for tool in await main.mcp.list_tools()}
    for name in (
        "create_milestone",
        "start_milestone",
        "drop_milestone",
        "add_milestone_spec_change",
        "remove_milestone_spec_change",
    ):
        assert name in tools
    for name in ("get_milestone", "list_milestones"):
        assert tools[name].annotations.readOnlyHint is True
