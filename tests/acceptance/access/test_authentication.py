"""Clients without valid authentication are refused and pointed at the OAuth flow."""

from __future__ import annotations

import re

import httpx
import pytest

from harness import Lachesis, scenario


@scenario("access", "Unauthenticated call")
async def test_unauthenticated_call(lachesis: Lachesis) -> None:
    client = Lachesis(lachesis.base_url, "not-a-token", lachesis.github)

    # The MCP client raises on a 401 before any tool result exists.
    with pytest.raises(ExceptionGroup) as raised:
        await client.call_tool("read_fuel", {})
    refusal, _ = raised.value.split(httpx.HTTPStatusError)
    assert refusal is not None, raised.value
    response = refusal.exceptions[0].response

    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    metadata_url = re.search(r'resource_metadata="([^"]+)"', challenge)
    assert metadata_url is not None, challenge
    async with httpx.AsyncClient(timeout=30) as http:
        metadata = (await http.get(metadata_url[1])).raise_for_status().json()
    assert metadata["authorization_servers"]
