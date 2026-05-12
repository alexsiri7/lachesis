"""Stub implementations for all 7 Lachesis MCP tools.

Real implementations are delivered in issues #4-#10.
"""

from __future__ import annotations

from typing import Any


async def list_requirements(repo: str) -> list[dict[str, Any]]:
    """Return all requirements for a given repo (id, title, status, github_issue)."""
    raise NotImplementedError("Implemented in issue #4")


async def get_requirement(repo: str, id: str) -> str:
    """Return the full markdown content of a single requirement file."""
    raise NotImplementedError("Implemented in issue #5")


async def create_requirement(repo: str, title: str, why: str, what: str) -> dict[str, Any]:
    """Create a new requirement file and return its path and auto-assigned id."""
    raise NotImplementedError("Implemented in issue #6")


async def update_requirement(repo: str, id: str, fields: dict[str, Any]) -> str:
    """Update fields (title, why, what, status) in an existing requirement."""
    raise NotImplementedError("Implemented in issue #7")


async def delete_requirement(repo: str, id: str) -> dict[str, Any]:
    """Delete a requirement. Refuses if status is 'done'."""
    raise NotImplementedError("Implemented in issue #8")


async def link_issues(repo: str, id: str, issues: list[dict[str, Any]]) -> str:
    """Write GitHub issue numbers into the requirement's Issues section."""
    raise NotImplementedError("Implemented in issue #9")


async def create_github_issues(repo: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one or more GitHub issues and return their numbers/titles/urls."""
    raise NotImplementedError("Implemented in issue #10")
