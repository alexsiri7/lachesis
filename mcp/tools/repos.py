"""Lachesis MCP tool implementations — repo registry management."""

from __future__ import annotations

import logging
from typing import Any

import db
from auth import current_db_user_id

logger = logging.getLogger(__name__)


def _require_user_id() -> str:
    """Return the authenticated Supabase user ID or raise."""
    user_id = current_db_user_id.get()
    if not user_id:
        raise RuntimeError(
            "No authenticated Supabase user in context — ensure lachesis_uid is in the JWT"
        )
    return user_id


async def register_repo(repo: str) -> dict[str, Any]:
    """Register a repo so Lachesis tracks it."""
    user_id = _require_user_id()
    try:
        return db.register_repo(user_id, repo)
    except Exception:
        logger.warning("register_repo failed user_id=%s repo=%s", user_id, repo)
        raise


async def unregister_repo(repo: str) -> dict[str, Any]:
    """Unregister a repo from Lachesis tracking."""
    user_id = _require_user_id()
    try:
        db.unregister_repo(user_id, repo)
    except Exception:
        logger.warning("unregister_repo failed user_id=%s repo=%s", user_id, repo)
        raise
    return {"unregistered": repo}


async def list_repos() -> list[dict[str, Any]]:
    """List all repos registered with Lachesis."""
    user_id = _require_user_id()
    try:
        return db.list_repos(user_id)
    except Exception:
        logger.warning("list_repos failed user_id=%s", user_id)
        raise
