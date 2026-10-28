"""Sessions survive restarts: a token issued before a redeploy keeps working after it."""

from __future__ import annotations

from harness import Lachesis, scenario


@scenario("access", "Deploy overnight")
async def test_deploy_overnight(lachesis: Lachesis) -> None:
    before = await lachesis.call_tool("read_fuel", {})
    assert not before.is_error, before.text

    await lachesis.restart()

    after = await lachesis.call_tool("read_fuel", {})
    assert not after.is_error, after.text
    assert "reserve_percent" in after.text
