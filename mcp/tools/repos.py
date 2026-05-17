"""Lachesis MCP tool implementations — repo registry management."""

from __future__ import annotations

import logging
import re
from typing import Any

import db
from auth import current_db_user_id

logger = logging.getLogger(__name__)

_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$"
)


def _normalize_repo(repo: str) -> str:
    repo = repo.strip()
    m = _GITHUB_URL_RE.match(repo)
    if m:
        return m.group(1)
    if "/" not in repo:
        raise ValueError("repo must be in owner/repo format, e.g. alexsiri7/lachesis")
    parts = repo.split("/")
    if len(parts) == 2 and parts[0] and parts[1]:
        return repo
    raise ValueError(f"invalid repo format: {repo!r}")


def _require_user_id() -> str:
    """Return the authenticated DB user ID or raise."""
    user_id = current_db_user_id.get()
    if not user_id:
        raise RuntimeError(
            "No DB user ID in request context. "
            "This may indicate a database connectivity issue (check server logs) "
            "or a token that predates the lachesis_uid claim."
        )
    return user_id


async def register_repo(repo: str) -> dict[str, Any]:
    """Register a repo so Lachesis tracks it."""
    user_id = _require_user_id()
    return db.register_repo(user_id, _normalize_repo(repo))


async def unregister_repo(repo: str) -> dict[str, Any]:
    """Unregister a repo from Lachesis tracking."""
    user_id = _require_user_id()
    db.unregister_repo(user_id, repo)
    return {"unregistered": repo}


async def list_repos() -> list[dict[str, Any]]:
    """List all repos registered with Lachesis."""
    user_id = _require_user_id()
    return db.list_repos(user_id)
