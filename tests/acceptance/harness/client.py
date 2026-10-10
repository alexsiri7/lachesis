"""What a test holds: an authenticated MCP client for the running app, and its stubs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from harness.github import FakeGitHub
from harness.ntfy import FakeNtfy


@dataclass(frozen=True)
class ToolResult:
    is_error: bool
    text: str

    def json(self) -> Any:
        return json.loads(self.text)


class Lachesis:
    def __init__(self, base_url: str, token: str, github: FakeGitHub, ntfy: FakeNtfy) -> None:
        self.base_url = base_url
        self.github = github
        self.ntfy = ntfy
        self._token = token

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        headers = {"Authorization": f"Bearer {self._token}"}
        async with (
            httpx.AsyncClient(headers=headers, timeout=30) as http,
            streamable_http_client(f"{self.base_url}/mcp", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool(name, arguments or {})
        if result.structuredContent is not None and not result.isError:
            text = json.dumps(_unwrap(result.structuredContent))
        else:
            text = "".join(c.text for c in result.content if isinstance(c, TextContent))
        return ToolResult(is_error=result.isError, text=text)

    async def web_page(self, path: str = "/web/") -> str:
        """GET a web page as the signed-in author, returning its HTML."""
        async with httpx.AsyncClient(
            base_url=self.base_url,
            cookies={"lachesis_web_session": self._token},
            timeout=30,
            follow_redirects=False,
        ) as http:
            response = await http.get(path)
        response.raise_for_status()
        return response.text


def _unwrap(structured: dict[str, Any]) -> Any:
    # FastMCP wraps a tool's non-object return value as {"result": value}.
    return structured["result"] if structured.keys() == {"result"} else structured
