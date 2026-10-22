"""Tests for mcp/tools/routing.py — which model runs each piece of work."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from auth import current_db_user_id
from tools.fuel import fuel_status
from tools.routing import failed_trial, prices, route, route_run, trial_tokens


@pytest.fixture(autouse=True)
def _no_accounts_or_overflow():
    """No registered accounts and no overflow report unless a test says otherwise."""
    with (
        patch("db.list_fuel", return_value=[]),
        patch("db.get_overflow", return_value=None),
    ):
        yield


_REPO = "alexsiri7/lachesis"
_DEEPSEEK = "deepseek/deepseek-chat"
_CLAUDE_PRICE = 5.0
_CHEAP = 0.0001  # per 110-token run: about 0.91 dollars per million tokens
_PRICEY = 0.001  # per 110-token run: about 9.09 dollars per million tokens
_RESETS_AT = datetime(2026, 10, 11, 9, tzinfo=UTC)
_NOW = _RESETS_AT - timedelta(days=2)
_CLOSED = {_REPO: set(range(1, 11))}


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


def _run(
    issue: int,
    kind: str = "triage",
    model: str = _DEEPSEEK,
    *,
    rework: int = 0,
    retries: int = 0,
    audit_findings: int = 0,
    tokens_in: int = 100,
    tokens_out: int = 10,
    cost_usd: float | None = None,
    repo_name: str = _REPO,
    size: int | None = None,
) -> dict[str, Any]:
    return {
        "id": issue,
        "repo_name": repo_name,
        "size": size,
        "issue": issue,
        "model": model,
        "kind": kind,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "rework": rework,
        "retries": retries,
        "audit_findings": audit_findings,
    }


def _trusted(
    model: str = _DEEPSEEK, kind: str = "triage", cost_usd: float | None = _CHEAP, runs: int = 5
) -> list[dict[str, Any]]:
    return [_run(issue, kind, model, cost_usd=cost_usd) for issue in range(1, runs + 1)]


def _fuel(
    used: float = 60.0, spent: float = 12.0, cap: float = 40.0, **row: object
) -> dict[str, Any]:
    return fuel_status(
        {
            "weekly_used_percent": used,
            "weekly_resets_at": _RESETS_AT,
            "shorter_limit_until": None,
            "overflow_spent_usd": spent,
            "overflow_cap_usd": cap,
            "reported_at": _NOW,
            **row,
        },
        _NOW,
    )


def _route(
    runs: list[dict[str, Any]],
    kind: str = "triage",
    fuel: dict[str, Any] | None = None,
    emergency: bool = False,
) -> dict[str, Any]:
    return route(kind, runs, _CLOSED, _fuel() if fuel is None else fuel, _CLAUDE_PRICE, emergency)


def test_cheap_model_for_triage() -> None:
    """Scenario: Cheap model for triage — a trusted model cheaper than Claude is chosen."""
    result = _route(_trusted())

    assert result["model"] == _DEEPSEEK
    assert result["via"] == "requesty"
    assert result["candidates"] == [
        {
            "model": _DEEPSEEK,
            "trial": False,
            "usd_per_million_tokens": 0.9091,
            "tokens_per_point": None,
            "usd_per_point": None,
        }
    ]
    assert result["trial"] is False
    assert result["claude_usd_per_million_tokens"] == _CLAUDE_PRICE


def test_a_cheap_model_not_yet_trusted_leaves_the_run_on_claude() -> None:
    result = _route(_trusted(runs=4))

    assert result["model"] == "claude"
    assert result["via"] == "allowance"
    assert result["candidates"] == []


def test_implementation_stays_on_claude_until_another_model_earns_trust_for_it() -> None:
    result = _route(_trusted(), kind="implementation")

    assert result["model"] == "claude"
    assert result["via"] == "allowance"
    assert result["candidates"] == []


def test_a_trusted_model_pricier_than_claude_is_chosen_only_once_the_allowance_is_spent() -> None:
    runs = _trusted(cost_usd=_PRICEY)

    assert _route(runs)["model"] == "claude"

    result = _route(runs, fuel=_fuel(used=85.0))
    assert result["model"] == _DEEPSEEK
    assert result["via"] == "requesty"
    assert result["claude_available"] is False


def test_a_shorter_limit_overflows_to_the_cheapest_trusted_model() -> None:
    runs = (
        _trusted(cost_usd=_PRICEY)
        + _trusted("qwen/qwen-coder", cost_usd=_PRICEY * 2)
        + _trusted("mistral/codestral", cost_usd=_PRICEY)
    )
    fuel = _fuel(shorter_limit_until=_NOW + timedelta(hours=3))

    result = _route(runs, fuel=fuel)

    assert result["model"] == _DEEPSEEK
    assert result["via"] == "requesty"
    assert [c["model"] for c in result["candidates"]] == [
        _DEEPSEEK,
        "mistral/codestral",
        "qwen/qwen-coder",
    ]


def test_with_the_allowance_spent_and_overflow_at_the_cap_no_model_is_chosen() -> None:
    result = _route(_trusted(cost_usd=_PRICEY), fuel=_fuel(used=85.0, spent=40.0))

    assert result["model"] is None
    assert result["via"] is None
    assert result["overflow_available"] is False
    assert "ask a budget question" in result["reason"]


def test_only_an_emergency_draws_on_the_reserve_when_no_overflow_model_is_trusted() -> None:
    fuel = _fuel(used=85.0)

    emergency = _route([], kind="implementation", fuel=fuel, emergency=True)
    assert emergency["model"] == "claude"
    assert emergency["via"] == "allowance"

    routine = _route([], kind="implementation", fuel=fuel)
    assert routine["model"] is None
    assert "wait for the Claude allowance to reset" in routine["reason"]


def test_not_even_an_emergency_draws_on_an_allowance_fully_spent() -> None:
    result = _route([], kind="implementation", fuel=_fuel(used=100.0), emergency=True)

    assert result["claude_available"] is False
    assert result["model"] is None


def test_with_no_fuel_reported_the_run_stays_on_claude() -> None:
    result = _route(_trusted(), fuel=fuel_status(None, _NOW))

    assert result["model"] == "claude"
    assert result["overflow_available"] is False


def test_a_week_that_has_reset_makes_claude_and_overflow_available_again() -> None:
    reset = fuel_status(
        {
            "weekly_used_percent": 100.0,
            "weekly_resets_at": _NOW - timedelta(hours=1),
            "shorter_limit_until": None,
            "overflow_spent_usd": 40.0,
            "overflow_cap_usd": 40.0,
            "reported_at": _NOW - timedelta(days=1),
        },
        _NOW,
    )

    result = _route(_trusted(), fuel=reset)

    assert result["claude_available"] is True
    assert result["overflow_available"] is True
    assert result["model"] == _DEEPSEEK


def test_models_with_no_reported_cost_are_never_candidates() -> None:
    result = _route(_trusted("qwen/qwen-coder", cost_usd=None))

    assert result["model"] == "claude"
    assert result["candidates"] == []


def test_a_smaller_claude_model_needs_cheaper_points_to_beat_claude() -> None:
    sonnet = "claude-sonnet"
    runs = _trusted(sonnet, cost_usd=None)

    result = _route(runs)

    assert result["model"] == "claude"
    assert [c["model"] for c in result["candidates"]] == [sonnet]


def _sized(
    model: str, kind: str, size: int, first: int, count: int = 5, **extra: Any
) -> list[dict[str, Any]]:
    return [_run(n, kind, model, size=size, **extra) for n in range(first, first + count)]


def _route_sized(runs: list[dict[str, Any]], kind: str, size: int) -> dict[str, Any]:
    closed = {_REPO: {run["issue"] for run in runs}}
    return route(kind, runs, closed, _fuel(), _CLAUDE_PRICE, False, size=size)


def test_a_smaller_claude_model_trusted_at_the_size_runs_on_the_allowance() -> None:
    """Scenario: Smaller model for small work — cheaper per point at a trusted size."""
    sonnet = "claude-sonnet"
    runs = _sized(sonnet, "implementation", 3, 1, tokens_in=20, tokens_out=10)
    runs += _sized("claude", "implementation", 3, 10, 2, tokens_in=60, tokens_out=30)

    result = _route_sized(runs, "implementation", 2)

    assert (result["model"], result["via"], result["trial"]) == (sonnet, "allowance", False)
    assert result["size"] == 2


def test_a_cheaper_trusted_claude_variant_beats_overflow_cheaper_than_claude() -> None:
    haiku = "claude-haiku"
    runs = _sized(haiku, "implementation", 2, 1, tokens_in=2, tokens_out=2)
    runs += _sized("claude", "implementation", 2, 10, 2, tokens_in=50, tokens_out=50)
    runs += _sized(_DEEPSEEK, "implementation", 2, 20, cost_usd=0.0003, tokens_in=50, tokens_out=50)

    result = _route_sized(runs, "implementation", 2)

    assert [c["model"] for c in result["candidates"]] == [haiku, _DEEPSEEK]
    assert (result["model"], result["via"]) == (haiku, "allowance")


def test_failures_at_a_smaller_size_take_a_model_off_it() -> None:
    """Scenario: Losing trust — clean runs at 3 points do not keep it routed at 1."""
    runs = _sized(_DEEPSEEK, "tests", 3, 1, cost_usd=_CHEAP)
    runs += _sized(_DEEPSEEK, "tests", 1, 10, 2, cost_usd=_CHEAP, rework=1)

    result = _route_sized(runs, "tests", 1)

    assert (result["model"], result["candidates"]) == ("claude", [])


def test_failures_at_a_smaller_size_inside_the_streak_keep_a_model_off_it() -> None:
    """Scenario: Losing trust — routing agrees with the scorecard when the failures at
    1 point come before the streak at 3 points completes."""
    runs = _sized(_DEEPSEEK, "tests", 3, 1, 4, cost_usd=_CHEAP)
    runs += _sized(_DEEPSEEK, "tests", 1, 10, 2, cost_usd=_CHEAP, rework=1)
    runs += _sized(_DEEPSEEK, "tests", 3, 20, 1, cost_usd=_CHEAP)

    for size in (1, 3):
        result = _route_sized(runs, "tests", size)
        assert (result["model"], result["candidates"]) == ("claude", [])


def test_trust_from_runs_never_sized_holds_at_one_point_and_trials_the_next() -> None:
    runs = _trusted(cost_usd=_CHEAP)

    one = _route_sized(runs, "triage", 1)
    two = _route_sized(runs, "triage", 2)
    three = _route_sized(runs, "triage", 3)

    assert (one["model"], one["trial"]) == (_DEEPSEEK, False)
    assert (two["model"], two["trial"]) == (_DEEPSEEK, True)
    assert three["model"] == "claude"


def test_cost_per_point_lets_a_pricier_model_win_on_fewer_tokens() -> None:
    runs = _sized(_DEEPSEEK, "tests", 2, 1, cost_usd=_PRICEY)
    runs += _sized("claude", "tests", 2, 10, 2, tokens_in=1000, tokens_out=1000)

    result = _route_sized(runs, "tests", 2)

    assert (result["model"], result["via"]) == (_DEEPSEEK, "requesty")
    assert result["candidates"][0]["usd_per_point"] == 0.0005


def test_a_trial_steps_up_one_size_above_the_trusted_one() -> None:
    """Scenario: Stepping up a size — trusted up to 2 points, trialled at 3, not at 5."""
    runs = _sized(_DEEPSEEK, "tests", 2, 1, cost_usd=_CHEAP)

    three = _route_sized(runs, "tests", 3)
    five = _route_sized(runs, "tests", 5)

    assert (three["model"], three["trial"]) == (_DEEPSEEK, True)
    assert (five["model"], five["trial"], five["candidates"]) == ("claude", False, [])


def test_a_new_kind_is_trialled_at_one_point_first() -> None:
    runs = _sized(_DEEPSEEK, "triage", 1, 1, cost_usd=_CHEAP)

    one = _route_sized(runs, "tests", 1)
    two = _route_sized(runs, "tests", 2)

    assert (one["model"], one["trial"]) == (_DEEPSEEK, True)
    assert two["model"] == "claude"


def test_a_trial_wins_a_tie_with_claude() -> None:
    """A Claude variant due a trial and priced like Claude runs, so it can earn trust."""
    haiku = "claude-haiku"
    runs = _sized(haiku, "tests", 1, 1)

    result = _route_sized(runs, "implementation", 1)

    assert (result["model"], result["trial"]) == (haiku, True)


def test_with_no_trials_only_a_trusted_model_runs() -> None:
    runs = _sized(_DEEPSEEK, "triage", 1, 1, cost_usd=_CHEAP)
    closed = {_REPO: {run["issue"] for run in runs}}

    result = route(
        "tests", runs, closed, _fuel(), _CLAUDE_PRICE, False, size=1, no_trials="a reason"
    )

    assert (result["model"], result["trial"], result["candidates"]) == ("claude", False, [])
    assert result["reason"].startswith("a reason, so only a trusted model runs it")


def test_a_failed_trial_is_one_whose_model_was_not_trusted_when_it_ran() -> None:
    """Scenario: Failed trial redone — only a failed run by a model on trial counts."""
    haiku = "claude-haiku"
    runs = _sized(haiku, "tests", 1, 1)
    runs.append(_run(20, "implementation", haiku, size=1, rework=1))
    runs.append(_run(21, "implementation", "claude", size=1, rework=1))
    runs.append(_run(22, "tests", haiku, size=1, rework=1))
    runs.append(_run(23, "implementation", haiku, size=1))
    closed = {_REPO: set(range(1, 6))}

    assert failed_trial(runs, closed, _REPO, 20, "implementation") is True
    assert failed_trial(runs, closed, _REPO, 21, "implementation") is False
    assert failed_trial(runs, closed, _REPO, 22, "tests") is False
    assert failed_trial(runs, closed, _REPO, 23, "implementation") is False
    assert failed_trial(runs, closed, _REPO, 24, "implementation") is False


def test_trial_tokens_count_only_the_given_runs_that_were_trials() -> None:
    runs = _sized(_DEEPSEEK, "triage", 1, 1, cost_usd=_CHEAP)  # the first five earn trust
    runs.append(_run(6, "triage", _DEEPSEEK, size=1, cost_usd=_CHEAP))
    runs.append(_run(7, "triage", "claude", size=1))
    closed = {_REPO: set(range(1, 8))}

    assert trial_tokens(runs, closed, {5, 6, 7}) == 110


def test_prices_are_reported_cost_over_total_tokens_per_million() -> None:
    runs = [
        _run(1, cost_usd=0.002, tokens_in=1500, tokens_out=500),
        _run(2, cost_usd=0.001, tokens_in=800, tokens_out=200),
        _run(3, cost_usd=None, tokens_in=100_000),
        _run(4, model="qwen/qwen-coder", cost_usd=None),
        _run(5, model="claude-opus-5-5", cost_usd=1.0),
    ]

    assert prices(runs) == {_DEEPSEEK: 1.0}


async def test_route_run_routes_from_the_users_runs_issues_and_fuel(set_user_id: str) -> None:
    issues = [{"number": n, "state": "closed", "is_pull_request": False} for n in range(1, 6)]
    row = {
        "weekly_used_percent": 60.0,
        "weekly_resets_at": datetime.now(UTC) + timedelta(days=2),
        "shorter_limit_until": None,
        "overflow_spent_usd": 12.0,
        "overflow_cap_usd": 40.0,
        "reported_at": datetime.now(UTC),
    }
    with (
        patch("tools.routing.db.list_runs", return_value=_trusted()) as list_runs,
        patch(
            "tools.scorecard.github.list_all_issues", new=AsyncMock(return_value=issues)
        ) as list_all_issues,
        patch("tools.routing.db.get_fuel", return_value=row) as get_fuel,
        patch("tools.fuel.db.list_fuel", return_value=[]),
        patch("db.get_overflow", return_value={"spent_usd": 12.0, "cap_usd": 40.0}),
    ):
        result = await route_run("triage")

    assert result["model"] == _DEEPSEEK
    assert result["via"] == "requesty"
    list_runs.assert_any_call("test-uid")
    get_fuel.assert_any_call("test-uid")
    list_all_issues.assert_awaited_once_with(_REPO)


async def test_route_run_refuses_a_size_that_is_not_a_fibonacci_point(set_user_id: str) -> None:
    with patch("tools.routing.db.list_runs") as list_runs:
        with pytest.raises(ValueError, match="size must be one of"):
            await route_run("tests", size=4)

    list_runs.assert_not_called()


async def test_route_run_refuses_an_unknown_kind(set_user_id: str) -> None:
    with patch("tools.routing.db.list_runs") as list_runs:
        with pytest.raises(ValueError, match="kind must be one of"):
            await route_run("deploy")  # type: ignore[arg-type]

    list_runs.assert_not_called()


def _status(name: str, *, primary: bool = False, **overrides: object) -> dict:
    return {
        "name": name,
        "primary": primary,
        "shorter_limit_in_force": False,
        "paused": False,
        "usable_percent": 10.0,
        "emergency_usable_percent": 30.0,
        "within_pace": True,
        **overrides,
    }


def test_choose_account_takes_the_one_with_most_allowance_the_primary_on_a_tie() -> None:
    from tools.routing import choose_account

    factory = _status("factory", primary=True)
    main = _status("main", usable_percent=30.0)
    assert choose_account([factory, main], False) == (main, False)
    assert choose_account([factory, _status("main")], False) == (factory, False)


def test_choose_account_skips_paused_limited_and_spent_accounts() -> None:
    from tools.routing import choose_account

    accounts = [
        _status("paused", paused=True, usable_percent=50.0),
        _status("limited", shorter_limit_in_force=True, usable_percent=50.0),
        _status("spent", usable_percent=0.0, emergency_usable_percent=5.0),
    ]
    assert choose_account(accounts, False) == (None, False)
    assert choose_account(accounts, True)[0]["name"] == "spent"


def test_choose_account_holds_ordinary_work_for_pace_but_not_emergencies() -> None:
    from tools.routing import choose_account

    ahead = _status("main", within_pace=False)
    assert choose_account([ahead], False) == (None, True)
    assert choose_account([ahead], True) == (ahead, False)


def test_overflow_never_reported_is_never_routed_to() -> None:
    fuel = {
        **fuel_status(None, _NOW),
        "overflow": {"reported": False, "at_cap": False},
    }
    result = _route(_trusted(), fuel=fuel)

    assert result["overflow_available"] is False
    assert result["via"] != "requesty"
