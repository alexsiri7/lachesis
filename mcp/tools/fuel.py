"""Lachesis MCP tool implementations — fuel: what each run spent and what is left."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import db

from .repos import _require_user_id
from .requirements import _resolve_repo

RESERVE_PERCENT = 20.0
_WEEK = timedelta(days=7)


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


def _parse_time(name: str, value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise ValueError(f"{name} must be an ISO-8601 time with a timezone, got {value!r}")
    return parsed


async def report_fuel(
    weekly_used_percent: float,
    weekly_resets_at: str,
    overflow_spent_usd: float,
    overflow_cap_usd: float,
    shorter_limit_until: str | None = None,
) -> dict[str, Any]:
    """Record the latest Claude allowance status and this week's Requesty overflow spend.

    Replaces the account's previous report. Returns fuel as :func:`read_fuel` does.
    """
    if not 0 <= weekly_used_percent <= 100:
        raise ValueError(
            f"weekly_used_percent must be between 0 and 100, got {weekly_used_percent}"
        )
    if overflow_spent_usd < 0:
        raise ValueError(f"overflow_spent_usd must not be negative, got {overflow_spent_usd}")
    if overflow_cap_usd < 0:
        raise ValueError(f"overflow_cap_usd must not be negative, got {overflow_cap_usd}")
    resets_at = _parse_time("weekly_resets_at", weekly_resets_at)
    now = datetime.now(UTC)
    if not now < resets_at <= now + _WEEK:
        raise ValueError(
            f"weekly_resets_at must be within the next 7 days, got {weekly_resets_at!r}"
        )
    limit_until = (
        None
        if shorter_limit_until is None
        else _parse_time("shorter_limit_until", shorter_limit_until)
    )
    user_id = _require_user_id()
    row = db.set_fuel(
        user_id, weekly_used_percent, resets_at, limit_until, overflow_spent_usd, overflow_cap_usd
    )
    return fuel_status(row, datetime.now(UTC))


def fuel_status(row: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    """Return what fuel is left at *now*, from the account's latest fuel report *row*.

    The reported week is the 7 days up to ``weekly_resets_at``. The pace is the share
    of the allowance outside the reserve that may be used by the end of today when
    it is spread evenly over the week.
    """
    if row is None:
        return {"reported": False, "reserve_percent": RESERVE_PERCENT}
    used = row["weekly_used_percent"]
    resets_at = row["weekly_resets_at"]
    limit_until = row["shorter_limit_until"]
    spent = row["overflow_spent_usd"]
    cap = row["overflow_cap_usd"]
    outside_reserve = 100 - RESERVE_PERCENT
    current = now < resets_at
    pace = None
    if current:
        day = min(max((now - (resets_at - _WEEK)) // timedelta(days=1) + 1, 1), 7)
        limit = round(outside_reserve * day / 7, 2)
        pace = {"day": day, "days": 7, "limit_percent": limit, "within_pace": used <= limit}
    return {
        "reported": True,
        "reported_at": row["reported_at"],
        "current": current,
        "allowance": {
            "used_percent": used,
            "resets_at": resets_at,
            "shorter_limit_until": limit_until,
            "shorter_limit_in_force": limit_until is not None and now < limit_until,
            "reserve_percent": RESERVE_PERCENT,
            "left_outside_reserve_percent": round(max(0.0, outside_reserve - used), 2),
            "only_reserve_left": used >= outside_reserve,
        },
        "overflow": {
            "spent_usd": spent,
            "cap_usd": cap,
            "left_usd": round(max(0.0, cap - spent), 2),
            "at_cap": spent >= cap,
        },
        "pace": pace,
    }


async def read_fuel() -> dict[str, Any]:
    """Return what fuel is left: the latest report, the reserve and today's pace."""
    return fuel_status(db.get_fuel(_require_user_id()), datetime.now(UTC))
