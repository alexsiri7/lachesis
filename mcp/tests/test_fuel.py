"""Tests for mcp/tools/fuel.py — run usage reports and what fuel is left."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from auth import current_db_user_id
from tools.fuel import (
    account_status,
    fuel_status,
    overflow_status,
    read_fuel,
    register_account,
    report_fuel,
    report_overflow,
    report_usage,
)


@pytest.fixture(autouse=True)
def _no_accounts_or_overflow():
    """No registered accounts and no overflow report unless a test says otherwise."""
    with (
        patch("db.list_fuel", return_value=[]),
        patch("db.get_overflow", return_value=None),
    ):
        yield


_REGISTERED = [{"id": "r1", "repo_name": "alexsiri7/lachesis", "created_at": "t"}]


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


_FACTORY = {
    "name": "factory",
    "share_percent": 100.0,
    "five_hour_pause_percent": None,
    "primary": True,
}
_MAIN = {"name": "main", "share_percent": 65.0, "five_hour_pause_percent": 60.0, "primary": False}


@pytest.fixture()
def registered():
    with (
        patch("tools.requirements.db.list_repos", return_value=_REGISTERED),
        patch("tools.fuel.db.list_accounts", return_value=[]),
    ):
        yield


@pytest.fixture()
def accounts():
    with (
        patch("tools.fuel.db.list_accounts", return_value=[_FACTORY, _MAIN]),
        patch("tools.fuel.db.list_fuel", return_value=[]),
        patch("tools.fuel.db.get_fuel", return_value=None),
    ):
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
        None,
    )


async def test_a_run_on_the_claude_allowance_has_no_cost(
    set_user_id: str, registered: None
) -> None:
    with patch("tools.fuel.db.record_usage_report", return_value={"id": 4}) as record:
        await report_usage("lachesis", 160, "claude-opus-5-5", "implementation", 900, 300)

    assert record.call_args[0][-2] is None


async def test_a_claude_run_naming_no_account_is_the_primary_accounts(
    set_user_id: str, registered: None
) -> None:
    with (
        patch("tools.fuel.db.list_accounts", return_value=[_MAIN, _FACTORY]),
        patch("tools.fuel.db.record_usage_report", return_value={"id": 4}) as record,
    ):
        await report_usage("lachesis", 160, "claude-opus-5-5", "implementation", 900, 300)

    assert record.call_args[0][-1] == "factory"


async def test_a_run_naming_an_unregistered_account_is_refused(
    set_user_id: str, registered: None, accounts: None
) -> None:
    with (
        patch("tools.fuel.db.record_usage_report") as record,
        pytest.raises(ValueError, match="registered: factory, main"),
    ):
        await report_usage(
            "lachesis", 160, "claude-opus-5-5", "implementation", 900, 300, account="nobody"
        )

    record.assert_not_called()


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
    assert allowance["reserve_percent"] == 10
    assert allowance["reserve_released"] is False
    assert allowance["left_outside_reserve_percent"] == 30
    assert allowance["only_reserve_left"] is False
    assert result["overflow"] == {
        "reported": True,
        "spent_usd": 12,
        "cap_usd": 40,
        "left_usd": 28,
        "at_cap": False,
    }


def test_an_early_burst_is_outside_the_pace() -> None:
    """Two days in, 70 percent of the allowance outside the reserve is over the day's share."""
    now = _WEEK_START + timedelta(days=2, hours=3)

    assert fuel_status(_fuel(used=63), now)["pace"] == {
        "day": 3,
        "days": 7,
        "limit_percent": 38.57,
        "within_pace": False,
    }
    assert fuel_status(_fuel(used=30), now)["pace"]["within_pace"] is True


def test_only_the_reserve_left() -> None:
    allowance = fuel_status(_fuel(used=90), _WEEK_START + timedelta(days=5))["allowance"]

    assert allowance["only_reserve_left"] is True
    assert allowance["left_outside_reserve_percent"] == 0


def test_the_reserve_is_released_in_the_last_24_hours_before_the_reset() -> None:
    held = fuel_status(_fuel(used=90), _RESETS_AT - timedelta(hours=25))
    released = fuel_status(_fuel(used=90), _RESETS_AT - timedelta(hours=20))

    assert held["allowance"]["reserve_released"] is False
    assert held["allowance"]["only_reserve_left"] is True
    assert released["allowance"]["reserve_released"] is True
    assert released["allowance"]["only_reserve_left"] is False
    assert released["allowance"]["left_outside_reserve_percent"] == 10
    assert released["pace"]["limit_percent"] == 100


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
    assert fuel_status(None, _WEEK_START) == {"reported": False, "reserve_percent": 10.0}
    with (
        patch("tools.fuel.db.get_fuel", return_value=None) as get,
        patch("tools.fuel.db.list_fuel", return_value=[]),
    ):
        assert await read_fuel() == {
            "reported": False,
            "reserve_percent": 10.0,
            "overflow": {
                "reported": False,
                "spent_usd": None,
                "cap_usd": None,
                "left_usd": None,
                "at_cap": False,
            },
            "accounts": [],
        }
    get.assert_called_once_with("test-uid")


async def test_report_fuel_stores_the_status(set_user_id: str) -> None:
    resets_at = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    limit_until = resets_at - timedelta(days=2)
    stored = _fuel(weekly_resets_at=resets_at, shorter_limit_until=limit_until)
    with (
        patch("tools.fuel.db.list_accounts", return_value=[_FACTORY]),
        patch("tools.fuel.db.set_fuel") as store,
        patch("tools.fuel.db.get_fuel", return_value=stored),
        patch("tools.fuel.db.list_fuel", return_value=[]),
    ):
        result = await report_fuel(
            60, resets_at.isoformat(), 12, 40, shorter_limit_until=limit_until.isoformat()
        )

    store.assert_called_once_with("test-uid", "factory", 60, resets_at, limit_until, None)
    assert result["reported"] is True
    assert result["allowance"]["resets_at"] == resets_at


async def test_report_fuel_accepts_a_shorter_limit_that_has_already_lifted(
    set_user_id: str,
) -> None:
    resets_at = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    lifted_at = (datetime.now(UTC) - timedelta(hours=2)).replace(microsecond=0)
    stored = _fuel(weekly_resets_at=resets_at, shorter_limit_until=lifted_at)
    with (
        patch("tools.fuel.db.list_accounts", return_value=[_FACTORY]),
        patch("tools.fuel.db.set_fuel") as store,
        patch("tools.fuel.db.get_fuel", return_value=stored),
        patch("tools.fuel.db.list_fuel", return_value=[]),
    ):
        result = await report_fuel(
            60, resets_at.isoformat(), 12, 40, shorter_limit_until=lifted_at.isoformat()
        )

    store.assert_called_once_with("test-uid", "factory", 60, resets_at, lifted_at, None)
    assert result["reported"] is True


def _in_days(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


@pytest.mark.parametrize(
    ("args", "kwargs", "field"),
    [
        ((-1, _in_days(3), 12, 40), {}, "weekly_used_percent"),
        ((101, _in_days(3), 12, 40), {}, "weekly_used_percent"),
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


async def test_the_first_report_registers_a_primary_factory_account(set_user_id: str) -> None:
    resets_at = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    with (
        patch("tools.fuel.db.list_accounts", return_value=[]),
        patch("tools.fuel.db.register_account", return_value=_FACTORY) as register,
        patch("tools.fuel.db.set_fuel") as store,
        patch("tools.fuel.db.get_fuel", return_value=None),
        patch("tools.fuel.db.list_fuel", return_value=[]),
    ):
        await report_fuel(10, resets_at.isoformat(), 0, 40)

    register.assert_called_once_with("test-uid", "factory", 100.0, None, True)
    assert store.call_args[0][1] == "factory"


async def test_report_fuel_names_the_account_and_its_five_hour_window(
    set_user_id: str, accounts: None
) -> None:
    resets_at = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    with patch("tools.fuel.db.set_fuel") as store:
        await report_fuel(
            20, resets_at.isoformat(), 0, 40, account=" main ", five_hour_used_percent=35
        )

    store.assert_called_once_with("test-uid", "main", 20, resets_at, None, 35)


async def test_report_fuel_refuses_an_unregistered_account(
    set_user_id: str, accounts: None
) -> None:
    with patch("tools.fuel.db.set_fuel") as store, pytest.raises(ValueError, match="nobody"):
        await report_fuel(20, _in_days(3), 0, 40, account="nobody")

    store.assert_not_called()


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"share_percent": 0}, "share_percent"),
        ({"share_percent": 101}, "share_percent"),
        ({"share_percent": 65, "five_hour_pause_percent": 0}, "five_hour_pause_percent"),
        ({"share_percent": 65, "name": " "}, "name"),
    ],
)
async def test_register_account_refuses_impossible_settings(
    set_user_id: str, kwargs: dict, field: str
) -> None:
    with (
        patch("tools.fuel.db.register_account") as register,
        pytest.raises(ValueError, match=field),
    ):
        await register_account(**{"name": "main", **kwargs})

    register.assert_not_called()


def _account(**overrides: object) -> dict:
    now = datetime.now(UTC)
    return {
        **_MAIN,
        "weekly_used_percent": 20.0,
        "weekly_resets_at": now + timedelta(days=3),
        "shorter_limit_until": None,
        "five_hour_used_percent": 10.0,
        "overflow_spent_usd": 0.0,
        "overflow_cap_usd": 40.0,
        "reported_at": now,
        **overrides,
    }


def test_an_accounts_reserve_and_usable_allowance_follow_its_share() -> None:
    status = account_status(_account(), datetime.now(UTC))

    assert status["reserve_percent"] == 6.5
    assert status["reserve_released"] is False
    assert status["usable_percent"] == 38.5
    assert status["emergency_usable_percent"] == 45
    assert status["paused"] is False
    assert status["within_pace"] is True


def test_an_accounts_reserve_is_usable_for_any_work_in_its_last_24_hours() -> None:
    now = datetime.now(UTC)
    status = account_status(
        _account(weekly_used_percent=60.0, weekly_resets_at=now + timedelta(hours=20)), now
    )

    assert status["reserve_released"] is True
    assert status["usable_percent"] == 5
    assert status["emergency_usable_percent"] == 5
    assert status["only_reserve_left"] is False
    # Never beyond the share: the whole 65 percent by the last day.
    assert status["pace"]["limit_percent"] == 65
    assert status["within_pace"] is True


def test_an_account_pauses_at_its_five_hour_threshold() -> None:
    now = datetime.now(UTC)
    assert account_status(_account(five_hour_used_percent=60.0), now)["paused"] is True
    assert account_status(_account(five_hour_used_percent=59.9), now)["paused"] is False


def test_an_accounts_pace_is_over_its_own_week_and_share() -> None:
    now = datetime.now(UTC)
    status = account_status(_account(weekly_resets_at=now + timedelta(days=6, hours=12)), now)

    # Day 1 of 7: a seventh of the 58.5 percent outside main's reserve.
    assert status["pace"]["limit_percent"] == round(58.5 / 7, 2)
    assert status["within_pace"] is False


def test_an_account_never_reported_or_reset_counts_as_unused() -> None:
    now = datetime.now(UTC)
    never = account_status(
        _account(
            reported_at=None,
            weekly_used_percent=None,
            weekly_resets_at=None,
            five_hour_used_percent=None,
        ),
        now,
    )
    reset = account_status(_account(weekly_resets_at=now - timedelta(hours=1)), now)

    assert never["used_percent"] == 0 and never["usable_percent"] == 58.5
    assert reset["used_percent"] == 0 and reset["current"] is False


async def test_report_fuel_ignores_overflow_figures(set_user_id: str, accounts: None) -> None:
    resets_at = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    with (
        patch("tools.fuel.db.set_fuel"),
        patch("tools.fuel.db.set_overflow") as set_overflow,
    ):
        await report_fuel(20, resets_at.isoformat(), 12, 40, account="main")

    set_overflow.assert_not_called()


async def test_report_overflow_stores_spending_and_cap(set_user_id: str) -> None:
    with (
        patch("tools.fuel.db.set_overflow") as store,
        patch("tools.fuel.db.get_overflow", return_value={"spent_usd": 3.0, "cap_usd": 10.0}),
        patch("tools.fuel.db.get_fuel", return_value=None),
    ):
        result = await report_overflow(3, 10)

    store.assert_called_once_with("test-uid", 3, 10)
    assert result["overflow"]["cap_usd"] == 10
    assert result["overflow"]["left_usd"] == 7


@pytest.mark.parametrize(("spent", "cap", "field"), [(-1, 10, "spent_usd"), (1, -1, "cap_usd")])
async def test_report_overflow_refuses_negative_amounts(
    set_user_id: str, spent: float, cap: float, field: str
) -> None:
    with patch("tools.fuel.db.set_overflow") as store, pytest.raises(ValueError, match=field):
        await report_overflow(spent, cap)

    store.assert_not_called()


def test_overflow_never_reported_has_no_cap_and_is_not_at_it() -> None:
    assert overflow_status(None, None) == {
        "reported": False,
        "spent_usd": None,
        "cap_usd": None,
        "left_usd": None,
        "at_cap": False,
    }
