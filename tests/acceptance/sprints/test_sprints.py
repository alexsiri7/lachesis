"""Sprints: the week that follows the primary account, and the budget every account adds to."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from harness import Lachesis, scenario

NOW = datetime.now(UTC)


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


async def _fuel(
    lachesis: Lachesis, account: str, used: float, resets_in: timedelta, cap: float
) -> None:
    await _ok(
        lachesis,
        "report_fuel",
        {
            "account": account,
            "weekly_used_percent": used,
            "weekly_resets_at": (NOW + resets_in).isoformat(),
            "overflow_spent_usd": 0,
            "overflow_cap_usd": cap,
        },
    )


@scenario("sprints", "Reading the sprint")
async def test_reading_the_sprint(lachesis: Lachesis) -> None:
    await _ok(
        lachesis, "register_account", {"name": "factory", "share_percent": 100, "primary": True}
    )
    await _ok(lachesis, "register_account", {"name": "main", "share_percent": 65})
    await _fuel(lachesis, "factory", 10, timedelta(days=3), cap=0)
    await _fuel(lachesis, "main", 10, timedelta(days=5), cap=0)

    sprint = await _ok(lachesis, "read_sprint", {})

    assert datetime.fromisoformat(sprint["end"]) == datetime.fromisoformat(
        (NOW + timedelta(days=3)).isoformat()
    )
    for key in ("start", "plan", "budget_tokens", "spent_tokens"):
        assert key in sprint


@scenario("sprints", "Shared account adds to the budget")
async def test_shared_account_adds_to_the_budget(lachesis: Lachesis) -> None:
    await _ok(
        lachesis, "register_account", {"name": "factory", "share_percent": 100, "primary": True}
    )
    await _ok(lachesis, "register_account", {"name": "main", "share_percent": 65})
    await _fuel(lachesis, "factory", 0, timedelta(days=3), cap=0)
    await _fuel(lachesis, "main", 0, timedelta(days=3), cap=0)

    sprint = await _ok(lachesis, "read_sprint", {})

    weekly = sprint["allowance"]["weekly_tokens"]
    # factory: 100% share less its 10 reserve = 90; main: 65% less 6.5 = 58.5.
    assert sprint["budget_tokens"] == round(weekly * 0.90) + round(weekly * 0.585)
