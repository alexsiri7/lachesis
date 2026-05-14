"""MCP tool functions for the repo registry (backed by lachesis_repos table)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import db
from auth import current_db_user_id

logger = logging.getLogger(__name__)


async def register_repo(repo: str) -> dict[str, Any]:
    """Register a repo in the user's registry. Idempotent."""
    user_id = current_db_user_id.get()
    if user_id is None:
        logger.warning("register_repo: unauthenticated call (current_db_user_id is None)")
        raise ValueError("User not authenticated")
    return await asyncio.to_thread(db.register_repo, user_id, repo)


async def unregister_repo(repo: str) -> dict[str, Any]:
    """Remove a repo from the user's registry. No-op if not registered."""
    user_id = current_db_user_id.get()
    if user_id is None:
        logger.warning("unregister_repo: unauthenticated call (current_db_user_id is None)")
        raise ValueError("User not authenticated")
    await asyncio.to_thread(db.unregister_repo, user_id, repo)
    return {"unregistered": repo}


async def list_repos() -> list[dict[str, Any]]:
    """Return all repos registered by the current user, ordered by repo_name."""
    user_id = current_db_user_id.get()
    if user_id is None:
        logger.warning("list_repos: unauthenticated call (current_db_user_id is None)")
        raise ValueError("User not authenticated")
    return await asyncio.to_thread(db.list_repos, user_id)
