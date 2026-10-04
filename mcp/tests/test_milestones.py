"""Tests for mcp/tools/milestones.py — milestones, whose state is read from the work."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import landing
import main
from auth import current_db_user_id
from derive import IssueFacts
from spec_reference import SpecReference
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
    linked_issues: list[int] | None = None,
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
            "linked_issues": linked_issues or [],
        },
    }
    if dropped:
        entry["reason"] = "its pull request closed without merging"
    return entry


def _progress(
    *entries: dict[str, Any],
    references: dict[int, SpecReference] | None = None,
    facts: dict[int, IssueFacts] | None = None,
):
    candidates = [landing.SpecChangeCandidate("", e) for e in entries]
    return patch(
        "tools.milestones.read_spec_progress",
        new=AsyncMock(return_value=SpecProgress({}, candidates, references or {}, facts or {})),
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
    for name in ("get_milestone", "list_milestones", "review_milestone"):
        assert tools[name].annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def _facts(number: int, *, state: str = "OPEN", merged: bool = False) -> IssueFacts:
    return {
        "number": number,
        "state": state,
        "updated_at": None,
        "closed_at": None,
        "labels": [],
        "closed_by_commit": merged,
        "blocked_by": [],
        "pulls": [],
    }


def _question(question_id: int, **about: Any) -> dict[str, Any]:
    return {
        "id": question_id,
        "repo_name": _REPO,
        "spec_change": None,
        "milestone": None,
        "blocks": [],
        **about,
    }


@pytest.fixture()
def requirements():
    with patch("tools.milestones.spec_change_requirements", new=AsyncMock(return_value=[])) as read:
        yield read


@pytest.fixture()
def questions():
    with patch("tools.milestones.db.list_open_questions", return_value=[]) as listed:
        yield listed


async def _review(*entries: dict[str, Any], row: dict[str, Any] | None = None, **progress: Any):
    with (
        patch("tools.milestones.db.get_milestone", return_value=row or _row()),
        _progress(*entries, **progress),
    ):
        return await milestones.review_milestone("lachesis", 7)


_IN_PROGRESS = _entry(170, status="in-progress", linked_issues=[231, 232])


@pytest.mark.usefixtures("deployment", "record_state", "questions")
async def test_requirement_without_work(requirements: AsyncMock) -> None:
    """Scenario: Requirement without work — the gaps name spec change 170 and the requirement."""
    requirements.return_value = [
        ("milestones", "Req A", "added"),
        ("milestones", "Req B", "changed"),
    ]
    references = {
        231: SpecReference(170, "milestones", "  req   B "),
        232: SpecReference(170, "milestones"),
        233: SpecReference(171, "milestones", "Req A"),
    }

    review = await _review(_IN_PROGRESS, references=references)

    requirements.assert_awaited_once_with(_REPO, 170)
    assert review["gaps"]["requirements_without_issues"] == [
        {"spec_change": 170, "capability": "milestones", "requirement": "Req A", "change": "added"}
    ]
    assert review["unreadable"] == []


@pytest.mark.usefixtures("deployment", "record_state", "questions", "requirements")
async def test_work_dropped() -> None:
    """Scenario: Work dropped — the gaps name the issue closed without a merged pull request."""
    facts = {
        230: _facts(230, state="CLOSED", merged=True),
        231: _facts(231, state="CLOSED"),
        232: _facts(232),
    }
    entry = _entry(170, status="in-progress", linked_issues=[232, 231, 230])

    review = await _review(entry, facts=facts)

    assert review["gaps"]["issues_closed_without_merge"] == [{"issue": 231, "spec_change": 170}]


@pytest.mark.usefixtures("deployment", "record_state", "questions")
async def test_gaps_follow_the_milestone_order(requirements: AsyncMock) -> None:
    requirements.side_effect = lambda _repo, number: [("milestones", f"Req {number}", "added")]
    facts = {200: _facts(200, state="CLOSED"), 300: _facts(300, state="CLOSED")}
    entries = (
        _entry(170, status="in-progress", linked_issues=[200]),
        _entry(172, status="in-progress", linked_issues=[300]),
    )

    review = await _review(*entries, row=_row(spec_changes=[172, 170]), facts=facts)

    assert review["gaps"]["issues_closed_without_merge"] == [
        {"issue": 300, "spec_change": 172},
        {"issue": 200, "spec_change": 170},
    ]
    assert [gap["spec_change"] for gap in review["gaps"]["requirements_without_issues"]] == [
        172,
        170,
    ]


@pytest.mark.usefixtures("record_state", "questions", "requirements")
@pytest.mark.parametrize("verdict", [None, {"state": "pending", "url": None}])
async def test_a_merge_not_yet_in_production(deployment: AsyncMock, verdict: dict | None) -> None:
    deployment.side_effect = lambda _repo, commit: (
        verdict if commit == "late" else {"state": "success", "url": "https://deploy/1"}
    )
    entries = [
        _entry(170, landed_commit="early"),
        _entry(172, landed_commit="late", landed_at="2026-10-03T00:00:00Z"),
        _entry(173, status="in-progress"),
    ]

    review = await _review(*entries, row=_row(spec_changes=[170, 172, 173]))

    assert review["gaps"]["merges_not_in_production"] == [
        {"spec_change": 172, "commit": "late", "deployment": verdict}
    ]


@pytest.mark.usefixtures("deployment", "record_state", "requirements")
async def test_open_questions_blocking_the_work(questions: AsyncMock) -> None:
    questions.return_value = [
        _question(1, blocks=[232]),
        _question(2, milestone="7"),
        _question(3, spec_change=170),
        _question(4, blocks=[999], milestone="8", spec_change=171),
        {**_question(5, blocks=[232]), "repo_name": "someone/else"},
    ]

    review = await _review(
        _IN_PROGRESS,
        references={232: SpecReference(170, "milestones"), 999: SpecReference(171, "milestones")},
    )

    assert [q["id"] for q in review["gaps"]["open_questions"]] == [1, 2, 3]


@pytest.mark.usefixtures("deployment", "record_state", "questions")
async def test_what_could_not_be_read_is_never_clean(requirements: AsyncMock) -> None:
    async def read(_repo: str, number: int) -> None:
        if number != 172:
            raise httpx.ConnectError("down")

    requirements.side_effect = read
    entries = [
        _entry(170, status="in-progress", linked_issues=[231]),
        _entry(171, status=None, source="unavailable", linked_issues=[232]),
        _entry(172, status="in-progress"),
    ]
    facts = {231: _facts(231, state="CLOSED"), 232: _facts(232, state="CLOSED")}

    review = await _review(*entries, row=_row(spec_changes=[170, 171, 172, 173]), facts=facts)

    assert review["unreadable"] == [170, 171, 172, 173]
    assert review["gaps"]["requirements_without_issues"] == []
    assert review["gaps"]["issues_closed_without_merge"] == [{"issue": 231, "spec_change": 170}]


@pytest.mark.usefixtures("deployment", "record_state", "questions")
async def test_a_closed_spec_change_has_no_requirements_to_review(
    requirements: AsyncMock,
) -> None:
    review = await _review(_entry(170, state="closed", status=None, source="unlinked"))

    requirements.assert_not_called()
    assert review["unreadable"] == []


async def test_review_refuses_a_milestone_that_has_not_started_or_was_dropped() -> None:
    for row, state in [(_row(started=False), "planned"), (_row(dropped=True), "dropped")]:
        with (
            patch("tools.milestones.db.get_milestone", return_value=row),
            _progress() as progress,
            pytest.raises(ValueError, match=f"Milestone 7 is {state}"),
        ):
            await milestones.review_milestone("lachesis", 7)

        progress.assert_not_called()
