"""Tests for mcp/tools/experiments.py — improvement experiments and their judgements."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import main
from auth import current_db_user_id
from tools.experiments import judge_experiment, record_experiment

_REPO = "owner/repo"
_CREATED = datetime(2026, 10, 10, tzinfo=UTC)


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def registered():
    with patch("tools.requirements.db.list_repos", return_value=[{"repo_name": _REPO}]):
        yield


def _row(**fields: Any) -> dict[str, Any]:
    return {
        "id": 1,
        "repo_name": _REPO,
        "title": "Self-review",
        "change": "Review the diff first.",
        "measure": "rework_rate",
        "baseline": 40.0,
        "target": 10.0,
        "issues": [50],
        "state": "open",
        "results": [],
        "created_at": _CREATED,
        **fields,
    }


async def _record(**fields: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "repo": _REPO,
        "title": "Self-review",
        "change": "Review the diff first.",
        "measure": "rework_rate",
        "baseline": 40,
        "target": 10,
        "issues": [50],
        **fields,
    }
    return await record_experiment(**arguments)


async def test_record_experiment_stores_it_open(set_user_id: str, registered: None) -> None:
    create = MagicMock(return_value=_row(issues=[50, 51]))
    with patch("db.create_experiment", create):
        experiment = await _record(issues=[51, 50, 51], title="  Self-review ")

    create.assert_called_once_with(
        "test-uid",
        _REPO,
        "Self-review",
        "Review the diff first.",
        "rework_rate",
        40,
        10,
        [50, 51],
    )
    assert experiment == {
        "id": 1,
        "repo": _REPO,
        "title": "Self-review",
        "change": "Review the diff first.",
        "measure": "rework_rate",
        "baseline": 40.0,
        "target": 10.0,
        "issues": [50, 51],
        "state": "open",
        "results": [],
        "created_at": _CREATED.isoformat(),
    }


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"change": " "}, "change must not be empty"),
        ({"measure": ""}, "measure must not be empty"),
        ({"issues": [0]}, "issues must be issue numbers"),
    ],
)
async def test_record_experiment_refuses_what_it_cannot_measure(
    set_user_id: str, registered: None, fields: dict[str, Any], message: str
) -> None:
    with patch("db.create_experiment") as create, pytest.raises(ValueError, match=message):
        await _record(**fields)

    create.assert_not_called()


async def test_record_experiment_refuses_an_unregistered_repo(set_user_id: str) -> None:
    with (
        patch("tools.requirements.db.list_repos", return_value=[]),
        patch("db.create_experiment") as create,
        pytest.raises(ValueError),
    ):
        await _record()

    create.assert_not_called()


async def test_judge_experiment_appends_the_judgement(set_user_id: str) -> None:
    result = {"value": 6, "verdict": "kept", "reason": "Fell.", "judged_at": "t"}
    judge = MagicMock(return_value=_row(state="kept", results=[result]))
    with patch("db.judge_experiment", judge):
        experiment = await judge_experiment(1, 6, "kept", " Fell. ")

    assert judge.call_args.args[:5] == ("test-uid", 1, 6, "kept", "Fell.")
    assert (experiment["state"], experiment["results"]) == ("kept", [result])


async def test_judge_experiment_needs_a_reason(set_user_id: str) -> None:
    with patch("db.judge_experiment") as judge, pytest.raises(ValueError, match="reason"):
        await judge_experiment(1, 6, "kept", "")

    judge.assert_not_called()


async def test_experiment_tools_are_registered_as_writes() -> None:
    tools = {tool.name: tool for tool in await main.mcp.list_tools()}
    for name in ("record_experiment", "judge_experiment"):
        annotations = tools[name].annotations
        assert annotations is None or not annotations.readOnlyHint
