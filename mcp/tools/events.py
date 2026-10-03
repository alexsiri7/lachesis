"""Lachesis MCP tool implementations — following the event log."""

from __future__ import annotations

from typing import Any

import db

from .repos import _require_user_id, normalize_repo

MAX_EVENTS_PER_READ = 500


async def list_events(
    after: int = 0,
    repo: str | None = None,
    kind: db.EventKind | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return the events after cursor *after*, oldest first, with the cursor for the next read."""
    user_id = _require_user_id()
    if after < 0:
        raise ValueError(f"after must be a cursor returned by list_events, got {after}")
    if not 1 <= limit <= MAX_EVENTS_PER_READ:
        raise ValueError(f"limit must be between 1 and {MAX_EVENTS_PER_READ}, got {limit}")
    repo_name = normalize_repo(repo) if repo is not None else None
    events = db.list_events(user_id, after, limit, repo_name=repo_name, kind=kind)
    cursor = events[-1]["id"] if events else after
    return {"events": events, "cursor": cursor}
