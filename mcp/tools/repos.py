"""Lachesis MCP tool implementations — repo registry management."""

from __future__ import annotations

import logging
from typing import Any

import db
from auth import require_db_user_id as _require_user_id

logger = logging.getLogger(__name__)


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
