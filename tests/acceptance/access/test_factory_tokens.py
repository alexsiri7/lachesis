"""Factory tokens: named, revocable keys a script presents to act as the author."""

from __future__ import annotations

import pytest

from harness import Lachesis, scenario


async def _create(lachesis: Lachesis, name: str) -> str:
    result = await lachesis.call_tool("create_factory_token", {"name": name})
    assert not result.is_error, result.text
    created = result.json()
    assert created["name"] == name
    return created["token"]


def _as(lachesis: Lachesis, token: str) -> Lachesis:
    return Lachesis(lachesis.base_url, token, lachesis.github)


@scenario("access", "A script reports with its token")
async def test_a_script_reports_with_its_token(lachesis: Lachesis) -> None:
    token = await _create(lachesis, "cron")

    result = await _as(lachesis, token).call_tool("read_fuel", {})
    listed = await lachesis.call_tool("list_factory_tokens", {})

    assert not result.is_error, result.text
    assert "reserve_percent" in result.text
    assert not listed.is_error, listed.text
    [entry] = listed.json()["tokens"]
    assert entry["name"] == "cron"
    assert entry["last_used_at"] is not None
    assert token not in listed.text


@scenario("access", "Revoked token")
async def test_revoked_token(lachesis: Lachesis) -> None:
    token = await _create(lachesis, "cron")
    revoked = await lachesis.call_tool("revoke_factory_token", {"name": "cron"})
    assert not revoked.is_error, revoked.text

    try:
        result = await _as(lachesis, token).call_tool("read_fuel", {})
        refused = result.is_error
    except Exception:  # a 401 ends the MCP session before any tool result
        refused = True

    assert refused


@scenario("access", "A token cannot mint tokens")
async def test_a_token_cannot_mint_tokens(lachesis: Lachesis) -> None:
    token = await _create(lachesis, "cron")

    result = await _as(lachesis, token).call_tool("create_factory_token", {"name": "more"})

    assert result.is_error
