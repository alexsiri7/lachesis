"""Fuel: each Claude account's allowance, the overflow cap, and what the factory may use."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness import Lachesis, scenario

REPO = "owner/repo"
NOW = datetime.now(UTC)


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


async def _register(lachesis: Lachesis, name: str, share: float, **extra: Any) -> Any:
    return await _ok(lachesis, "register_account", {"name": name, "share_percent": share, **extra})


async def _two_accounts(lachesis: Lachesis) -> None:
    await _register(lachesis, "factory", 100, primary=True)
    await _register(lachesis, "main", 65, five_hour_pause_percent=60)


async def _report(
    lachesis: Lachesis,
    account: str | None,
    used: float,
    *,
    five_hour: float | None = None,
    resets_in: timedelta = timedelta(days=3),
) -> Any:
    arguments: dict[str, Any] = {
        "weekly_used_percent": used,
        "weekly_resets_at": (NOW + resets_in).isoformat(),
    }
    if account is not None:
        arguments["account"] = account
    if five_hour is not None:
        arguments["five_hour_used_percent"] = five_hour
    return await _ok(lachesis, "report_fuel", arguments)


async def _only_reserve_left(lachesis: Lachesis, *, factory_resets_in: timedelta) -> None:
    # The reserve is 10 percent of each share: 10 of the factory's 100, 6.5 of main's 65.
    await _report(lachesis, "factory", 92, resets_in=factory_resets_in)
    await _report(lachesis, "main", 60)


async def _overflow(lachesis: Lachesis, spent: float, cap: float) -> Any:
    return await _ok(lachesis, "report_overflow", {"spent_usd": spent, "cap_usd": cap})


def _account(fuel: dict[str, Any], name: str) -> dict[str, Any]:
    return next(a for a in fuel["accounts"] if a["name"] == name)


def _issue(number: int, **fields: Any) -> dict[str, Any]:
    return {
        "number": number,
        "id": 2000 + number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "body": "",
        "state": "open",
        "labels": [],
        "assignees": [],
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "created_at": (NOW - timedelta(days=2)).isoformat(),
        "updated_at": (NOW - timedelta(days=1)).isoformat(),
        "closed_at": None,
        "state_reason": None,
        **fields,
    }


def _closed_issue(number: int) -> dict[str, Any]:
    return _issue(
        number,
        state="closed",
        closed_at=(NOW - timedelta(days=1)).isoformat(),
        state_reason="completed",
    )


async def _route(lachesis: Lachesis, kind: str, *, emergency: bool = False) -> Any:
    return await _ok(lachesis, "route_run", {"kind": kind, "emergency": emergency})


@scenario("fuel", "Registering a shared account")
async def test_registering_a_shared_account(lachesis: Lachesis) -> None:
    await _two_accounts(lachesis)

    accounts = {a["name"]: a for a in (await _ok(lachesis, "list_accounts", {}))["accounts"]}

    assert accounts["factory"]["primary"] is True
    assert accounts["factory"]["share_percent"] == 100
    assert accounts["main"]["primary"] is False
    assert accounts["main"]["share_percent"] == 65
    assert accounts["main"]["five_hour_pause_percent"] == 60


@scenario("fuel", "Run on overflow model")
async def test_run_on_overflow_model(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": REPO})

    report = await _ok(
        lachesis,
        "report_usage",
        {
            "repo": REPO,
            "issue": 160,
            "model": "deepseek/deepseek-chat",
            "kind": "triage",
            "tokens_in": 1000,
            "tokens_out": 200,
            "cost_usd": 0.01,
        },
    )

    assert report["issue"] == 160
    assert report["model"] == "deepseek/deepseek-chat"
    assert report["cost_usd"] == 0.01
    assert report["kind"] == "triage"


@scenario("fuel", "Run on a shared account")
async def test_run_on_a_shared_account(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": REPO})
    await _two_accounts(lachesis)
    usage = {
        "repo": REPO,
        "issue": 161,
        "model": "claude-opus-5-5",
        "kind": "implementation",
        "tokens_in": 5000,
        "tokens_out": 900,
    }

    report = await _ok(lachesis, "report_usage", {**usage, "account": "main"})
    unknown = await lachesis.call_tool("report_usage", {**usage, "account": "nobody"})

    assert report["issue"] == 161
    assert report["account"] == "main"
    assert report["kind"] == "implementation"
    assert unknown.is_error


@pytest.mark.xfail(strict=True, reason="spec change #357")
@scenario("fuel", "Reading fuel")
async def test_reading_fuel(lachesis: Lachesis) -> None:
    await _two_accounts(lachesis)
    await _report(lachesis, "factory", 60)
    await _report(lachesis, "main", 20)
    await _overflow(lachesis, 12, 40)

    fuel = await _ok(lachesis, "read_fuel", {})

    factory, main = _account(fuel, "factory"), _account(fuel, "main")
    assert factory["used_percent"] == 60
    assert main["used_percent"] == 20
    assert factory["resets_at"] and main["resets_at"]
    assert factory["reserve_percent"] == 10  # 10% of a 100% share
    assert main["reserve_percent"] == 6.5  # 10% of a 65% share
    assert main["usable_percent"] == 38.5  # 65 - 6.5 - 20
    assert fuel["overflow"]["spent_usd"] == 12
    assert fuel["overflow"]["cap_usd"] == 40


@scenario("fuel", "Reports do not overwrite each other")
async def test_reports_do_not_overwrite_each_other(lachesis: Lachesis) -> None:
    await _two_accounts(lachesis)
    await _report(lachesis, "factory", 60)

    await _report(lachesis, "main", 20)

    assert _account(await _ok(lachesis, "read_fuel", {}), "factory")["used_percent"] == 60


@scenario("fuel", "Only emergencies use the reserve")
async def test_only_emergencies_use_the_reserve(lachesis: Lachesis) -> None:
    await _two_accounts(lachesis)
    await _overflow(lachesis, 40, 40)
    await _only_reserve_left(lachesis, factory_resets_in=timedelta(days=3))

    ordinary = await _route(lachesis, "implementation")
    emergency = await _route(lachesis, "implementation", emergency=True)

    assert ordinary["model"] is None
    assert emergency["via"] == "allowance"
    assert emergency["account"] in {"factory", "main"}


@pytest.mark.xfail(strict=True, reason="spec change #357")
@scenario("fuel", "Unused reserve released before the reset")
async def test_unused_reserve_released_before_the_reset(lachesis: Lachesis) -> None:
    await lachesis.call_tool("register_repo", {"repo": REPO})
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=[_issue(1)])
    lachesis.github.on("GET", f"/repos/{REPO}/issues/1", json=_issue(1))
    await _two_accounts(lachesis)
    await _overflow(lachesis, 40, 40)
    await _only_reserve_left(lachesis, factory_resets_in=timedelta(hours=20))

    offered = await _ok(lachesis, "next_issue", {"repo": REPO})
    routed = await _route(lachesis, "implementation")

    assert offered["issue"]["number"] == 1
    assert routed["via"] == "allowance"
    assert routed["account"] == "factory"


@scenario("fuel", "The share is never exceeded")
async def test_the_share_is_never_exceeded(lachesis: Lachesis) -> None:
    await _two_accounts(lachesis)
    await _report(lachesis, "factory", 10)
    await _report(lachesis, "main", 65)

    routed = await _route(lachesis, "implementation", emergency=True)

    assert routed["account"] != "main"


@scenario("fuel", "Five-hour window too full")
async def test_five_hour_window_too_full(lachesis: Lachesis) -> None:
    await _two_accounts(lachesis)
    await _report(lachesis, "factory", 70)
    await _report(lachesis, "main", 5, five_hour=62)

    routed = await _route(lachesis, "implementation")
    fuel = await _ok(lachesis, "read_fuel", {})

    assert routed["account"] != "main"
    assert _account(fuel, "main")["paused"] is True


@scenario("fuel", "Reaching the cap")
async def test_reaching_the_cap(lachesis: Lachesis) -> None:
    await _two_accounts(lachesis)
    await _overflow(lachesis, 40, 40)
    await _only_reserve_left(lachesis, factory_resets_in=timedelta(days=3))

    routed = await _route(lachesis, "implementation")

    assert routed["model"] is None
    assert "cap" in routed["reason"]


@scenario("fuel", "Early burst held back")
async def test_early_burst_held_back(lachesis: Lachesis) -> None:
    # Two days into main's week (it resets in five): its usable share outside the
    # reserve is 58.5 percent of the allowance, and 70 percent of that is 40.95.
    await _two_accounts(lachesis)
    await _report(lachesis, "factory", 79)
    await _report(lachesis, "main", 40.95, resets_in=timedelta(days=5))

    fuel = await _ok(lachesis, "read_fuel", {})
    routed = await _route(lachesis, "implementation")

    assert _account(fuel, "main")["within_pace"] is False
    assert routed["account"] != "main"


@scenario("fuel", "Account reports leave overflow alone")
async def test_account_reports_leave_overflow_alone(lachesis: Lachesis) -> None:
    await _two_accounts(lachesis)
    await _overflow(lachesis, 12, 40)

    await _report(lachesis, "main", 20)

    overflow = (await _ok(lachesis, "read_fuel", {}))["overflow"]
    assert overflow["spent_usd"] == 12
    assert overflow["cap_usd"] == 40


@scenario("fuel", "Overflow never reported")
async def test_overflow_never_reported(lachesis: Lachesis) -> None:
    # A cheap model trusted for triage: five clean triage runs on closed issues.
    await lachesis.call_tool("register_repo", {"repo": REPO})
    lachesis.github.on("GET", f"/repos/{REPO}/issues", json=[_closed_issue(n) for n in range(1, 6)])
    for number in range(1, 6):
        await _ok(
            lachesis,
            "report_usage",
            {
                "repo": REPO,
                "issue": number,
                "model": "deepseek/deepseek-chat",
                "kind": "triage",
                "tokens_in": 10_000,
                "tokens_out": 1_000,
                "cost_usd": 0.001,
            },
        )
    await _two_accounts(lachesis)
    await _only_reserve_left(lachesis, factory_resets_in=timedelta(days=3))

    routed = await _route(lachesis, "triage")

    assert routed["via"] != "requesty"
    assert routed["model"] != "deepseek/deepseek-chat"
