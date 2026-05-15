"""Lachesis MCP tool implementations — repo registry management."""

from __future__ import annotations

import logging
from typing import Any

import db
from auth import current_db_user_id

logger = logging.getLogger(__name__)


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
    return db.register_repo(user_id, repo)


async def unregister_repo(repo: str) -> dict[str, Any]:
    """Unregister a repo from Lachesis tracking."""
    user_id = _require_user_id()
    db.unregister_repo(user_id, repo)
    return {"unregistered": repo}


async def list_repos() -> list[dict[str, Any]]:
    """List all repos registered with Lachesis."""
    user_id = _require_user_id()
    return db.list_repos(user_id)
