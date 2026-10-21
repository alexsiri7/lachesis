"""The site root sends a browser to the web view and leaves MCP clients as they were.

A browser is told apart by asking for ``text/html``. The MCP client's unauthorized response
is the one every unauthenticated MCP request gets: 401, ``{"error": "unauthorized"}`` and a
``WWW-Authenticate`` header naming the protected resource metadata.
"""

from __future__ import annotations

import httpx
import pytest

from harness import Lachesis, scenario

BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
MCP_ACCEPT = "application/json, text/event-stream"


@pytest.mark.xfail(strict=True, reason="spec change #382")
@scenario("progress-reporting", "Browser at the root")
async def test_browser_at_the_root(lachesis: Lachesis) -> None:
    async with httpx.AsyncClient(base_url=lachesis.base_url, follow_redirects=True) as http:
        response = await http.get("/", headers={"Accept": BROWSER_ACCEPT})

    assert response.history, response.text
    first = response.history[0]
    assert httpx.URL(first.headers["location"]).path == "/web/"
    assert response.url.path == "/web/login"
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "accounts.google.com" in response.text


@scenario("progress-reporting", "MCP client at the root")
async def test_mcp_client_at_the_root(lachesis: Lachesis) -> None:
    async with httpx.AsyncClient(base_url=lachesis.base_url) as http:
        posted = await http.post("/", json={}, headers={"Accept": MCP_ACCEPT})
        fetched = await http.get("/", headers={"Accept": MCP_ACCEPT})

    for response in (posted, fetched):
        assert response.status_code == 401
        assert response.json() == {"error": "unauthorized"}
        assert "resource_metadata=" in response.headers["www-authenticate"]
