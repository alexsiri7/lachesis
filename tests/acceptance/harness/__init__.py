"""The only way acceptance tests reach Lachesis: MCP tools over the protocol, GitHub stubbed."""

from harness.client import Lachesis, ToolResult
from harness.github import FakeGitHub
from harness.markers import scenario

__all__ = ["FakeGitHub", "Lachesis", "ToolResult", "scenario"]
