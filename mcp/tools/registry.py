"""MCP tool functions for the repo registry (backed by lachesis_repos table)."""

from __future__ import annotations

from typing import Any

import db
from auth import current_db_user_id


async def register_repo(repo: str) -> dict[str, Any]:
    """Register a repo in the user's registry. Idempotent."""
    user_id = current_db_user_id.get()
    if user_id is None:
        raise ValueError("User not authenticated")
    return db.register_repo(user_id, repo)


async def unregister_repo(repo: str) -> dict[str, Any]:
    """Remove a repo from the user's registry. No-op if not registered."""
    user_id = current_db_user_id.get()
    if user_id is None:
        raise ValueError("User not authenticated")
    db.unregister_repo(user_id, repo)
    return {"unregistered": repo}


async def list_repos() -> list[dict[str, Any]]:
    """Return all repos registered by the current user."""
    user_id = current_db_user_id.get()
    if user_id is None:
        raise ValueError("User not authenticated")
    return db.list_repos(user_id)
