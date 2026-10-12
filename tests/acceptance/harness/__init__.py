"""The only way acceptance tests reach Lachesis: MCP tools, with GitHub and ntfy stubbed."""

from harness.client import Lachesis, ToolResult
from harness.find import find_matching
from harness.github import FakeGitHub
from harness.markers import scenario
from harness.ntfy import FakeNtfy, Notification

__all__ = [
    "FakeGitHub",
    "FakeNtfy",
    "Lachesis",
    "Notification",
    "ToolResult",
    "find_matching",
    "scenario",
]
