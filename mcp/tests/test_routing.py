"""Tests for mcp/tools/routing.py — which model runs each piece of work."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from auth import current_db_user_id
from tools.fuel import fuel_status
from tools.routing import prices, route, route_run

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
) -> dict[str, Any]:
    return {
        "repo_name": repo_name,
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
    assert result["candidates"] == [{"model": _DEEPSEEK, "usd_per_million_tokens": 0.9091}]
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


def test_claude_models_and_models_with_no_reported_cost_are_never_candidates() -> None:
    runs = _trusted("anthropic/claude-sonnet-5", cost_usd=_CHEAP) + _trusted(
        "qwen/qwen-coder", cost_usd=None
    )

    result = _route(runs)

    assert result["model"] == "claude"
    assert result["candidates"] == []


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
    ):
        result = await route_run("triage")

    assert result["model"] == _DEEPSEEK
    assert result["via"] == "requesty"
    list_runs.assert_called_once_with("test-uid")
    get_fuel.assert_called_once_with("test-uid")
    list_all_issues.assert_awaited_once_with(_REPO)


async def test_route_run_refuses_an_unknown_kind(set_user_id: str) -> None:
    with patch("tools.routing.db.list_runs") as list_runs:
        with pytest.raises(ValueError, match="kind must be one of"):
            await route_run("deploy")  # type: ignore[arg-type]

    list_runs.assert_not_called()
