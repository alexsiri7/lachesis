"""Lachesis MCP tool implementations — experiments: how the factory improves itself, one
measured change at a time, judged at each sprint review."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import db

from .repos import _require_user_id, normalize_repo
from .requirements import _resolve_repo


def experiment_view(row: dict[str, Any]) -> dict[str, Any]:
    """Return a stored experiment as the tools show it."""
    return {
        "id": row["id"],
        "repo": normalize_repo(row["repo_name"]),
        "title": row["title"],
        "change": row["change"],
        "measure": row["measure"],
        "baseline": row["baseline"],
        "target": row["target"],
        "issues": list(row["issues"]),
        "state": row["state"],
        "results": row["results"],
        "created_at": row["created_at"].isoformat(),
    }


def _require_text(name: str, value: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value.strip()


async def record_experiment(
    repo: str,
    title: str,
    change: str,
    measure: str,
    baseline: float,
    target: float,
    issues: list[int],
) -> dict[str, Any]:
    """Record an open experiment in a registered *repo*: the *change* to how the factory
    works, the *measure* it should move from *baseline* to *target*, and the *issues*
    holding its work, which read_sprint plans ahead of the ordinary backlog.

    Raises ``ValueError`` for an unregistered repo, empty text or an issue number below 1.
    """
    repo = await _resolve_repo(repo, require_registered=True)
    if any(issue < 1 for issue in issues):
        raise ValueError(f"issues must be issue numbers, got {issues!r}")
    row = db.create_experiment(
        _require_user_id(),
        repo,
        _require_text("title", title),
        _require_text("change", change),
        _require_text("measure", measure),
        baseline,
        target,
        sorted(set(issues)),
    )
    return experiment_view(row)


async def judge_experiment(
    id: int, value: float, verdict: db.ExperimentVerdict, reason: str
) -> dict[str, Any]:
    """Judge the open experiment *id* by its measure's *value* now: ``kept``,
    ``reverted``, or ``extended`` for one more sprint, at most once, for the *reason* given.

    Raises ``ValueError`` for an unknown verdict, an empty reason, or an experiment that
    is not found, not open, or already extended when extending it again.
    """
    row = db.judge_experiment(
        _require_user_id(),
        id,
        value,
        verdict,
        _require_text("reason", reason),
        datetime.now(UTC),
    )
    return experiment_view(row)
