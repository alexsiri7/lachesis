"""Lachesis MCP tool implementations — work notes on issues and milestones."""

from __future__ import annotations

from typing import Any, get_args

import db

from .repos import _require_user_id
from .requirements import _resolve_repo


def _require_one_subject(issue: int | None, milestone: str | None) -> None:
    if (issue is None) == (milestone is None):
        raise ValueError("name exactly one of issue or milestone")


async def add_note(
    repo: str,
    role: str,
    type: db.NoteType,
    text: str,
    issue: int | None = None,
    milestone: str | None = None,
) -> dict[str, Any]:
    """Add a note by *role* to exactly one *issue* or *milestone* in *repo*.

    Notes are never edited or deleted; a correction is a new note.
    Returns the stored note.
    """
    role = role.strip()
    text = text.strip()
    _require_one_subject(issue, milestone)
    if not role:
        raise ValueError("role must not be empty")
    if not text:
        raise ValueError("text must not be empty")
    if type not in get_args(db.NoteType):
        raise ValueError(f"type must be one of {', '.join(get_args(db.NoteType))}, got {type!r}")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    return db.record_note(user_id, repo, role, type, text, issue=issue, milestone=milestone)


async def list_notes(
    repo: str, issue: int | None = None, milestone: str | None = None
) -> list[dict[str, Any]]:
    """Return the notes on exactly one *issue* or *milestone* in *repo*, oldest first."""
    _require_one_subject(issue, milestone)
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    return db.list_notes(user_id, repo, issue=issue, milestone=milestone)
