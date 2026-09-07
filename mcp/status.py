"""Requirement status vocabulary shared by the MCP tools and the web dashboard."""

from __future__ import annotations

import re

VALID_STATUSES: frozenset[str] = frozenset(
    {"draft", "idea", "planned", "in-progress", "done", "cancelled", "superseded"}
)

INACTIVE_STATUSES: frozenset[str] = frozenset({"cancelled", "superseded"})


def canonical_status(value: object) -> str:
    """Fold a stored status into its canonical spelling.

    Case, surrounding whitespace and underscore/space separators are normalised,
    so ``in_progress`` and ``In-Progress`` both become ``in-progress``.  A value
    with no canonical form is normalised but not mapped — no spelling is invented.
    """
    normalized = str(value or "").strip().lower()
    return re.sub(r"[\s_]+", "-", normalized)
