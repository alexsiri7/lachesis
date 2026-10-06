"""Model routing: which model a run uses, and which Claude account pays for it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness import Lachesis, scenario

NOW = datetime.now(UTC)


async def _ok(lachesis: Lachesis, tool: str, arguments: dict[str, Any]) -> Any:
    result = await lachesis.call_tool(tool, arguments)
    assert not result.is_error, result.text
    return result.json()


async def _accounts_reporting(
    lachesis: Lachesis, *, factory: float, main: float, **main_extra: Any
) -> None:
    await _ok(
        lachesis, "register_account", {"name": "factory", "share_percent": 100, "primary": True}
    )
    await _ok(
        lachesis,
        "register_account",
        {"name": "main", "share_percent": 65, "five_hour_pause_percent": 60},
    )
    for name, used, extra in (("factory", factory, {}), ("main", main, main_extra)):
        await _ok(
            lachesis,
            "report_fuel",
            {
                "account": name,
                "weekly_used_percent": used,
                "weekly_resets_at": (NOW + timedelta(hours=12)).isoformat(),
                "overflow_spent_usd": 0,
                "overflow_cap_usd": 40,
                **extra,
            },
        )


@pytest.mark.xfail(strict=True, reason="spec change #256")
@scenario("model-routing", "Two accounts available")
async def test_two_accounts_available(lachesis: Lachesis) -> None:
    # factory: 80 - 70 = 10 usable; main: 52 - 22 = 30 usable.
    await _accounts_reporting(lachesis, factory=70, main=22)

    routed = await _ok(lachesis, "route_run", {"kind": "implementation"})

    assert routed["via"] == "allowance"
    assert routed["account"] == "main"


@pytest.mark.xfail(strict=True, reason="spec change #256")
@scenario("model-routing", "Shared account paused")
async def test_shared_account_paused(lachesis: Lachesis) -> None:
    await _accounts_reporting(lachesis, factory=50, main=5, five_hour_used_percent=61)

    routed = await _ok(lachesis, "route_run", {"kind": "implementation"})

    assert routed["via"] == "allowance"
    assert routed["account"] == "factory"
