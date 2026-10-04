"""Lachesis MCP tool implementations — fuel: what each run spent."""

from __future__ import annotations

from typing import Any, get_args

import db

from .repos import _require_user_id
from .requirements import _resolve_repo


async def report_usage(
    repo: str,
    issue: int,
    model: str,
    kind: db.UsageKind,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float | None = None,
) -> dict[str, Any]:
    """Record one run's usage on *issue* in *repo*.

    *cost_usd* is the money spent where the model is paid per token, and is left
    out for a run on the Claude allowance. Returns the stored report.
    """
    model = model.strip()
    if issue < 1:
        raise ValueError(f"a usage report must name an issue, got {issue}")
    if not model:
        raise ValueError("model must not be empty")
    if kind not in get_args(db.UsageKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(db.UsageKind))}, got {kind!r}")
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("tokens_in and tokens_out must not be negative")
    if cost_usd is not None and cost_usd < 0:
        raise ValueError(f"cost_usd must not be negative, got {cost_usd}")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    return db.record_usage_report(
        user_id, repo, issue, model, kind, tokens_in, tokens_out, cost_usd
    )
