"""Lachesis MCP tool implementations — routing: which model runs each piece of work."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args

import db
from settings import settings

from .fuel import accounts_status, fuel_status
from .repos import _require_user_id
from .scorecard import closed_issues, scorecard

CLAUDE = "claude"


def is_claude(model: str) -> bool:
    return CLAUDE in model.lower()


def prices(runs: list[dict[str, Any]]) -> dict[str, float]:
    """Return each overflow model's observed price in dollars per million tokens, from the
    *runs* that reported a cost. Models with no such run have no known price."""
    cost: dict[str, float] = {}
    tokens: dict[str, int] = {}
    for run in runs:
        if is_claude(run["model"]) or run["cost_usd"] is None:
            continue
        cost[run["model"]] = cost.get(run["model"], 0.0) + run["cost_usd"]
        tokens[run["model"]] = tokens.get(run["model"], 0) + run["tokens_in"] + run["tokens_out"]
    return {
        model: round(cost[model] / tokens[model] * 1_000_000, 4)
        for model in sorted(cost)
        if tokens[model] > 0
    }


def fuel_available(fuel: dict[str, Any], emergency: bool) -> tuple[bool, bool]:
    """Return whether the Claude allowance and the Requesty overflow may be used, from a
    :func:`fuel_status` result. Only an *emergency* may draw on the reserve.

    With no report the allowance is assumed available and the overflow cap unknown; once
    the reported week has reset, both start again.
    """
    if not fuel["reported"]:
        return True, False
    if not fuel["current"]:
        return True, fuel["overflow"]["cap_usd"] > 0
    allowance = fuel["allowance"]
    if allowance["shorter_limit_in_force"] or allowance["used_percent"] >= 100:
        claude = False
    elif allowance["only_reserve_left"]:
        claude = emergency
    else:
        claude = True
    return claude, not fuel["overflow"]["at_cap"]


def choose_account(
    accounts: list[dict[str, Any]], emergency: bool
) -> tuple[dict[str, Any] | None, bool]:
    """Return the Claude account a run should draw on, from :func:`accounts_status`, and
    whether some account was passed over only for being ahead of its pace.

    An account qualifies when no shorter limit is in force, its five-hour window has not
    paused it, and it has allowance the run may use: outside the reserve and within its
    pace, or for an *emergency* anything up to its share. Of those, the one with the most
    such allowance left, the primary winning a tie.
    """
    usable = "emergency_usable_percent" if emergency else "usable_percent"
    open_accounts = [
        account
        for account in accounts
        if not account["shorter_limit_in_force"] and not account["paused"] and account[usable] > 0
    ]
    paced = [a for a in open_accounts if emergency or a["within_pace"]]
    if not paced:
        return None, bool(open_accounts)
    return max(paced, key=lambda a: (a[usable], a["primary"])), False


def route(
    kind: str,
    runs: list[dict[str, Any]],
    closed: dict[str, set[int]],
    fuel: dict[str, Any],
    claude_price: float,
    emergency: bool,
    accounts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Choose the model for a *kind* run: the cheapest overflow model trusted for *kind*
    that costs less than *claude_price* while overflow is available, else Claude on the
    allowance, else the cheapest trusted overflow model, else none.

    With registered *accounts*, Claude is available only through an account
    :func:`choose_account` picks, and the choice names it as ``account``."""
    known = prices(runs)
    trusted = [
        entry["model"]
        for entry in scorecard(runs, closed)
        if not is_claude(entry["model"])
        and entry["model"] in known
        and any(score["kind"] == kind and score["trusted"] for score in entry["kinds"])
    ]
    candidates = sorted(trusted, key=lambda model: (known[model], model))
    claude_available, overflow_available = fuel_available(fuel, emergency)
    account: dict[str, Any] | None = None
    held_by_pace = False
    if accounts:
        account, held_by_pace = choose_account(accounts, emergency)
        claude_available = account is not None

    def choose(model: str | None, via: str | None, reason: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "model": model,
            "via": via,
            "account": account["name"] if account is not None and via == "allowance" else None,
            "reason": reason,
            "claude_available": claude_available,
            "overflow_available": overflow_available,
            "claude_usd_per_million_tokens": claude_price,
            "candidates": [
                {"model": name, "usd_per_million_tokens": known[name]} for name in candidates
            ],
        }

    cheaper = [model for model in candidates if known[model] < claude_price]
    if overflow_available and cheaper:
        return choose(
            cheaper[0], "requesty", f"{cheaper[0]} is trusted for {kind} and cheaper than Claude"
        )
    if claude_available:
        reason = "Claude is trusted for every kind of work"
        if account is not None:
            reason += f"; account {account['name']} has the most allowance the run may use"
        return choose(CLAUDE, "allowance", reason)
    if held_by_pace:
        return choose(
            None,
            None,
            "every Claude account with allowance left is ahead of its pace: wait until "
            "spending is back within the pace",
        )
    if overflow_available and candidates:
        return choose(
            candidates[0],
            "requesty",
            "the Claude allowance outside the reserve is spent or a shorter limit is in force; "
            "overflow through Requesty",
        )
    if candidates:
        return choose(
            None,
            None,
            "the Requesty cap is reached: ask a budget question, then continue with the fuel "
            "left or wait for the reset",
        )
    if fuel["reported"] and fuel["current"] and fuel["overflow"]["at_cap"]:
        return choose(
            None,
            None,
            f"no Claude account has allowance left for {kind} and the Requesty cap is reached: "
            "ask a budget question, then wait for a reset",
        )
    return choose(
        None,
        None,
        f"no model trusted for {kind} has fuel: wait for the Claude allowance to reset",
    )


async def route_run(kind: db.UsageKind, emergency: bool = False) -> dict[str, Any]:
    """Return the model for a run of *kind*, derived from the scorecard, the observed
    prices and the latest fuel report. *emergency* lets the run draw on the reserve."""
    if kind not in get_args(db.UsageKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(db.UsageKind))}, got {kind!r}")
    user_id = _require_user_id()
    runs = db.list_runs(user_id)
    closed = await closed_issues(runs)
    now = datetime.now(UTC)
    fuel = fuel_status(db.get_fuel(user_id), now)
    accounts = accounts_status(user_id, now)
    return route(
        kind, runs, closed, fuel, settings.claude_usd_per_million_tokens, emergency, accounts
    )
