"""Lachesis MCP tool implementations — sprints: the week's budget and what fits in it."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import db
from settings import settings

from .fuel import RESERVE_PERCENT, fuel_status
from .repos import _require_user_id, normalize_repo
from .requirements import _NEEDS_AUTHOR_LABEL, _SPRINT, _backlog_order, _sprint_start

logger = logging.getLogger(__name__)


def _usd_to_tokens(usd: float, usd_per_million: float) -> int:
    return round(usd / usd_per_million * 1_000_000) if usd_per_million > 0 else 0


def sprint_budget(
    row: dict[str, Any] | None,
    now: datetime,
    allowance_tokens: int,
    usd_per_million: float,
) -> dict[str, Any] | None:
    """Return the sprint holding *now* and its budget in tokens, from the fuel report *row*.

    The budget is the weekly allowance outside the reserve plus the overflow cap,
    converted at the Claude price. Once the reported week has reset, nothing is
    known to be spent in the new one. ``None`` when fuel was never reported.
    """
    if row is None:
        return None
    fuel = fuel_status(row, now)
    start = _sprint_start(row["weekly_resets_at"], now)
    outside_reserve = 100 - RESERVE_PERCENT
    current = fuel["current"]
    used = row["weekly_used_percent"]
    cap = row["overflow_cap_usd"]
    spent_usd = row["overflow_spent_usd"]

    allowance_budget = round(allowance_tokens * outside_reserve / 100)
    allowance_spent = round(allowance_tokens * min(used, outside_reserve) / 100) if current else 0
    overflow_budget = _usd_to_tokens(cap, usd_per_million)
    overflow_spent = _usd_to_tokens(min(spent_usd, cap), usd_per_million) if current else 0

    budget = allowance_budget + overflow_budget
    spent = allowance_spent + overflow_spent
    return {
        "start": start,
        "end": start + _SPRINT,
        "budget_tokens": budget,
        "spent_tokens": spent,
        "left_tokens": max(0, budget - spent),
        "within_pace": fuel["pace"]["within_pace"] if fuel["pace"] else None,
        "allowance": {
            "weekly_tokens": allowance_tokens,
            "reserve_percent": RESERVE_PERCENT,
            "used_percent": used,
            "budget_tokens": allowance_budget,
            "spent_tokens": allowance_spent,
        },
        "overflow": {
            "cap_usd": cap,
            "spent_usd": spent_usd,
            "usd_per_million_tokens": usd_per_million,
            "budget_tokens": overflow_budget,
            "spent_tokens": overflow_spent,
        },
    }


def estimates_by_issue(user_id: str) -> dict[tuple[str, int], int | None]:
    """Return each estimated issue's tokens, keyed by ``(repo, issue)``."""
    return {
        (normalize_repo(row["repo_name"]), row["issue"]): row["tokens"]
        for row in db.list_estimates(user_id)
    }


def fits(estimate: int | None, left_tokens: int) -> bool:
    """Whether work estimated at *estimate* fits *left_tokens*.

    An issue with no estimate fits while any budget is left: with no history every
    estimate is missing, and leaving them out would stall all work.
    """
    return left_tokens > 0 and (estimate or 0) <= left_tokens


def plan_sprint(
    entries: list[dict[str, Any]],
    estimates: dict[tuple[str, int], int | None],
    left_tokens: int,
) -> tuple[list[dict[str, Any]], int]:
    """Walk *entries* in backlog order, planning each whose estimate fits what is left.

    Parked work and work waiting on the author are not eligible. An entry that does
    not fit is skipped and the walk goes on, so smaller later work uses what is left.
    Returns the planned entries and how many eligible entries were not planned.
    """
    planned: list[dict[str, Any]] = []
    not_planned = 0
    for entry in entries:
        if not entry["ready"] or _NEEDS_AUTHOR_LABEL in entry["labels"]:
            continue
        estimate = estimates.get((entry["repo"], entry["number"]))
        if not fits(estimate, left_tokens):
            not_planned += 1
            continue
        left_tokens -= estimate or 0
        planned.append(
            {
                "repo": entry["repo"],
                "number": entry["number"],
                "title": entry["title"],
                "tier": entry["tier"],
                "estimate_tokens": estimate,
            }
        )
    return planned, not_planned


async def read_sprint() -> dict[str, Any]:
    """Return the current sprint: its dates, budget, spending and plan."""
    user_id = _require_user_id()
    now = datetime.now(UTC)
    budget = sprint_budget(
        db.get_fuel(user_id),
        now,
        settings.claude_weekly_allowance_tokens,
        settings.claude_usd_per_million_tokens,
    )
    if budget is None:
        return {
            "reported": False,
            "explanation": "No fuel has been reported, so there is no sprint yet — call "
            "report_fuel.",
        }

    entries: list[tuple[tuple[Any, ...], int, dict[str, Any]]] = []
    unreadable: list[str] = []
    repos = [normalize_repo(row["repo_name"]) for row in db.list_repos(user_id)]
    for index, repo in enumerate(repos):
        try:
            order = await _backlog_order(repo, user_id, now)
        except Exception:
            logger.warning("read_sprint: could not read the backlog of %s", repo)
            unreadable.append(repo)
            continue
        entries.extend((item["sort_key"], index, {**item, "repo": repo}) for item in order)
    # Tiers and creation times compare across repos; registration order only breaks a tie.
    entries.sort(key=lambda entry: entry[:2])

    plan, not_planned = plan_sprint(
        [entry for _, _, entry in entries], estimates_by_issue(user_id), budget["left_tokens"]
    )
    return {
        "reported": True,
        **budget,
        "plan": plan,
        "planned_tokens": sum(item["estimate_tokens"] or 0 for item in plan),
        "not_planned": not_planned,
        "unreadable": unreadable,
    }
