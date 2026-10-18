"""What a test holds: an authenticated MCP client for the running app, and its stubs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
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
    def __init__(
        self,
        base_url: str,
        token: str,
        github: FakeGitHub,
        ntfy: FakeNtfy,
        restart: Callable[[], None] | None = None,
    ) -> None:
        self.base_url = base_url
        self.github = github
        self.ntfy = ntfy
        self._token = token
        self._restart = restart

    async def restart(self) -> None:
        """Restart the server: nothing the app held in memory survives, the database does."""
        if self._restart is None:
            raise RuntimeError("this client was not given the running app to restart")
        await asyncio.to_thread(self._restart)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        headers = {"Authorization": f"Bearer {self._token}"}
        async with (
            httpx.AsyncClient(headers=headers, timeout=30) as http,
            streamable_http_client(f"{self.base_url}/mcp", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    async def list_tools(self) -> dict[str, dict[str, Any]]:
        """Each available tool's name, with the JSON schema of its arguments."""
        async with self._session() as session:
            result = await session.list_tools()
        return {tool.name: tool.inputSchema for tool in result.tools}

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        async with self._session() as session:
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
