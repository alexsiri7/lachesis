"""Lachesis MCP tool implementations — focus: how the author steers the computed backlog."""

from __future__ import annotations

from typing import Any

import db

from .repos import _require_user_id
from .requirements import _resolve_repo


def _all_focuses(user_id: str) -> list[dict[str, Any]]:
    return [
        {"repo": row["repo_name"], "milestone": row["milestone_id"]}
        for row in db.list_focus(user_id)
    ]


async def set_focus(repo: str, milestone: int | None = None) -> list[dict[str, Any]]:
    """Focus *repo*, or its *milestone*, putting their issues in the backlog's focus tier.

    Raises ``ValueError`` for an unregistered repo or a milestone that is not in it or
    was dropped. Returns every focus as ``{"repo", "milestone"}``, ``milestone`` being
    ``None`` for a repository focus.
    """
    repo = await _resolve_repo(repo, require_registered=True)
    user_id = _require_user_id()
    if milestone is not None:
        row = db.get_milestone(user_id, repo, milestone)
        if row is None:
            raise ValueError(f"Milestone {milestone} not found in {repo}")
        if row["dropped_at"] is not None:
            raise ValueError(f"Milestone {milestone} in {repo} was dropped")
    db.add_focus(user_id, repo, milestone)
    return _all_focuses(user_id)


async def clear_focus(repo: str, milestone: int | None = None) -> list[dict[str, Any]]:
    """Clear the focus on *repo*, or on its *milestone*, and nothing else.

    Clearing a focus that is not set changes nothing. Returns every focus left, as
    ``set_focus`` does.
    """
    repo = await _resolve_repo(repo, require_registered=True)
    user_id = _require_user_id()
    db.remove_focus(user_id, repo, milestone)
    return _all_focuses(user_id)
