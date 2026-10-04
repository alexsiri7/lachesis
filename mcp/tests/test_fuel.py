"""Tests for mcp/tools/fuel.py — run usage reports and what fuel is left."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from auth import current_db_user_id
from tools.fuel import fuel_status, read_fuel, report_fuel, report_usage

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


async def test_overflow_run_is_stored_with_model_tokens_cost_issue_and_kind(
    set_user_id: str, registered: None
) -> None:
    """Scenario: Run on overflow model — stored with model, tokens, cost, issue 160 and kind."""
    stored = {"id": 3, "issue": 160, "model": "deepseek/deepseek-chat", "cost_usd": 0.42}
    with patch("tools.fuel.db.record_usage_report", return_value=stored) as record:
        result = await report_usage(
            "lachesis", 160, " deepseek/deepseek-chat ", "tests", 120_000, 8_000, cost_usd=0.42
        )

    assert result == stored
    record.assert_called_once_with(
        "test-uid",
        "alexsiri7/lachesis",
        160,
        "deepseek/deepseek-chat",
        "tests",
        120_000,
        8_000,
        0.42,
    )


async def test_a_run_on_the_claude_allowance_has_no_cost(
    set_user_id: str, registered: None
) -> None:
    with patch("tools.fuel.db.record_usage_report", return_value={"id": 4}) as record:
        await report_usage("lachesis", 160, "claude-opus-5-5", "implementation", 900, 300)

    assert record.call_args[0][-1] is None


@pytest.mark.parametrize(
    ("issue", "model", "kind", "tokens_in", "tokens_out", "cost", "match"),
    [
        (0, "m", "tests", 1, 1, None, "must name an issue"),
        (160, "  ", "tests", 1, 1, None, "model"),
        (160, "m", "coding", 1, 1, None, "kind must be one of"),
        (160, "m", "tests", -1, 1, None, "tokens"),
        (160, "m", "tests", 1, -1, None, "tokens"),
        (160, "m", "tests", 1, 1, -0.01, "cost_usd"),
    ],
)
async def test_refuses_an_incomplete_report(
    set_user_id: str,
    registered: None,
    issue: int,
    model: str,
    kind: str,
    tokens_in: int,
    tokens_out: int,
    cost: float | None,
    match: str,
) -> None:
    with (
        patch("tools.fuel.db.record_usage_report") as record,
        pytest.raises(ValueError, match=match),
    ):
        await report_usage("lachesis", issue, model, kind, tokens_in, tokens_out, cost)  # type: ignore[arg-type]

    record.assert_not_called()


async def test_refuses_an_unregistered_repository(set_user_id: str, registered: None) -> None:
    with (
        patch("tools.fuel.db.record_usage_report") as record,
        pytest.raises(ValueError, match="not registered"),
    ):
        await report_usage("owner/other", 1, "m", "triage", 1, 1)

    record.assert_not_called()


_RESETS_AT = datetime(2026, 10, 11, 9, tzinfo=UTC)
_WEEK_START = _RESETS_AT - timedelta(days=7)


def _fuel(used: float = 60.0, spent: float = 12.0, cap: float = 40.0, **row: object) -> dict:
    return {
        "weekly_used_percent": used,
        "weekly_resets_at": _RESETS_AT,
        "shorter_limit_until": None,
        "overflow_spent_usd": spent,
        "overflow_cap_usd": cap,
        "reported_at": _WEEK_START + timedelta(days=4),
        **row,
    }


def test_reading_fuel_returns_both_figures_with_the_reset_time_and_the_reserve() -> None:
    """Scenario: Reading fuel — 60 percent used and 12 of a 40 dollar cap spent."""
    result = fuel_status(_fuel(), _WEEK_START + timedelta(days=5))

    assert result["reported"] is True
    assert result["current"] is True
    allowance = result["allowance"]
    assert allowance["used_percent"] == 60
    assert allowance["resets_at"] == _RESETS_AT
    assert allowance["reserve_percent"] == 20
    assert allowance["left_outside_reserve_percent"] == 20
    assert allowance["only_reserve_left"] is False
    assert result["overflow"] == {"spent_usd": 12, "cap_usd": 40, "left_usd": 28, "at_cap": False}


def test_an_early_burst_is_outside_the_pace() -> None:
    """Two days in, 70 percent of the allowance outside the reserve is over the day's share."""
    now = _WEEK_START + timedelta(days=2, hours=3)

    assert fuel_status(_fuel(used=56), now)["pace"] == {
        "day": 3,
        "days": 7,
        "limit_percent": 34.29,
        "within_pace": False,
    }
    assert fuel_status(_fuel(used=30), now)["pace"]["within_pace"] is True


def test_only_the_reserve_left() -> None:
    allowance = fuel_status(_fuel(used=80), _WEEK_START + timedelta(days=6))["allowance"]

    assert allowance["only_reserve_left"] is True
    assert allowance["left_outside_reserve_percent"] == 0


def test_the_overflow_cap_reached() -> None:
    overflow = fuel_status(_fuel(spent=41), _WEEK_START)["overflow"]

    assert overflow["at_cap"] is True
    assert overflow["left_usd"] == 0


def test_a_shorter_limit_is_in_force_until_it_lifts() -> None:
    now = _WEEK_START + timedelta(days=3)

    lifting = fuel_status(_fuel(shorter_limit_until=now + timedelta(hours=2)), now)
    lifted = fuel_status(_fuel(shorter_limit_until=now - timedelta(hours=2)), now)

    assert lifting["allowance"]["shorter_limit_in_force"] is True
    assert lifted["allowance"]["shorter_limit_in_force"] is False


def test_a_report_from_a_past_week_is_not_current() -> None:
    result = fuel_status(_fuel(), _RESETS_AT)

    assert result["current"] is False
    assert result["pace"] is None


async def test_nothing_reported(set_user_id: str) -> None:
    assert fuel_status(None, _WEEK_START) == {"reported": False, "reserve_percent": 20.0}
    with patch("tools.fuel.db.get_fuel", return_value=None) as get:
        assert await read_fuel() == {"reported": False, "reserve_percent": 20.0}
    get.assert_called_once_with("test-uid")


async def test_report_fuel_stores_the_status(set_user_id: str) -> None:
    resets_at = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    limit_until = resets_at - timedelta(days=2)
    stored = _fuel(weekly_resets_at=resets_at, shorter_limit_until=limit_until)
    with patch("tools.fuel.db.set_fuel", return_value=stored) as store:
        result = await report_fuel(
            60, resets_at.isoformat(), 12, 40, shorter_limit_until=limit_until.isoformat()
        )

    store.assert_called_once_with("test-uid", 60, resets_at, limit_until, 12, 40)
    assert result["reported"] is True
    assert result["allowance"]["resets_at"] == resets_at


async def test_report_fuel_accepts_a_shorter_limit_that_has_already_lifted(
    set_user_id: str,
) -> None:
    resets_at = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    lifted_at = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0)
    stored = _fuel(weekly_resets_at=resets_at, shorter_limit_until=lifted_at)
    with patch("tools.fuel.db.set_fuel", return_value=stored) as store:
        result = await report_fuel(
            60, resets_at.isoformat(), 12, 40, shorter_limit_until=lifted_at.isoformat()
        )

    store.assert_called_once_with("test-uid", 60, resets_at, lifted_at, 12, 40)
    assert result["reported"] is True


def _in_days(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


@pytest.mark.parametrize(
    ("args", "kwargs", "field"),
    [
        ((-1, _in_days(3), 12, 40), {}, "weekly_used_percent"),
        ((101, _in_days(3), 12, 40), {}, "weekly_used_percent"),
        ((60, _in_days(3), -1, 40), {}, "overflow_spent_usd"),
        ((60, _in_days(3), 12, -1), {}, "overflow_cap_usd"),
        ((60, "2026-10-11T09:00:00", 12, 40), {}, "weekly_resets_at"),
        ((60, "next tuesday", 12, 40), {}, "weekly_resets_at"),
        ((60, _in_days(-1), 12, 40), {}, "weekly_resets_at"),
        ((60, _in_days(8), 12, 40), {}, "weekly_resets_at"),
        ((60, _in_days(3), 12, 40), {"shorter_limit_until": "soon"}, "shorter_limit_until"),
    ],
)
async def test_report_fuel_refuses_an_impossible_report(
    set_user_id: str, args: tuple, kwargs: dict, field: str
) -> None:
    with (
        patch("tools.fuel.db.set_fuel") as store,
        pytest.raises(ValueError, match=field),
    ):
        await report_fuel(*args, **kwargs)

    store.assert_not_called()
