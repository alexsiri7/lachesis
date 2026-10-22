"""Lachesis MCP tool implementations — routing: which model runs each piece of work."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args

import db
from settings import settings

from .fuel import read
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
    overflow = fuel.get("overflow") or {"reported": False}
    if not overflow.get("reported", True):
        overflow_ok = False
    elif fuel["reported"] and not fuel["current"]:
        overflow_ok = overflow["cap_usd"] > 0
    else:
        overflow_ok = not overflow["at_cap"]
    if not fuel["reported"]:
        return True, overflow_ok
    if not fuel["current"]:
        return True, overflow_ok
    allowance = fuel["allowance"]
    if allowance["shorter_limit_in_force"] or allowance["used_percent"] >= 100:
        claude = False
    elif allowance["only_reserve_left"]:
        claude = emergency
    else:
        claude = True
    return claude, overflow_ok


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


def _trial(score: dict[str, Any], size: int | None) -> bool | None:
    """Return whether a model with *score* for a kind runs work of *size* there as a trial,
    False when it is trusted for it, None when it may not run it.

    With no *size* only kind trust counts. Otherwise a model trusted at *size* or larger
    runs it, and a trial steps up one size above the largest it is trusted at, starting
    at 1 point of a kind the model may trial.
    """
    if size is None:
        return False if score["trusted"] else None
    up_to = score["trusted_up_to"]
    if up_to is not None and size <= up_to:
        return False
    index = db.SIZES.index(size)
    below = db.SIZES[index - 1] if index else None
    if up_to is not None and up_to == below:
        return True
    if up_to is None and below is None and score["may_trial"]:
        return True
    return None


def options(
    kind: str,
    size: int | None,
    runs: list[dict[str, Any]],
    closed: dict[str, set[int]],
    claude_price: float,
) -> list[dict[str, Any]]:
    """Return the models a *kind* run of *size* may use, cheapest first.

    CLAUDE is trusted for every kind and size; any other model must earn trust, or be due
    a trial, as :func:`_trial` says. An overflow model also needs an observed price;
    Claude models are priced at *claude_price*. The cost of a model is its tokens per
    point delivered on *kind* at its price, when every model has delivered points of
    *kind*, else its price per token. On a tie a trusted model goes before a trial, Claude
    before overflow, and CLAUDE first.
    """
    known = prices(runs)
    scores = {
        entry["model"]: next(score for score in entry["kinds"] if score["kind"] == kind)
        for entry in scorecard(runs, closed)
    }
    found = [_option(CLAUDE, False, scores.get(CLAUDE), claude_price)]
    for model, score in scores.items():
        if model == CLAUDE or not (is_claude(model) or model in known):
            continue
        trial = _trial(score, size)
        if trial is not None:
            found.append(_option(model, trial, score, known.get(model, claude_price)))
    by_point = all(option["usd_per_point"] is not None for option in found)
    return sorted(
        found,
        key=lambda option: (
            option["usd_per_point"] if by_point else option["usd_per_million_tokens"],
            option["trial"],
            not is_claude(option["model"]),
            option["model"] != CLAUDE,
            option["model"],
        ),
    )


def _option(model: str, trial: bool, score: dict[str, Any] | None, price: float) -> dict[str, Any]:
    tokens_per_point = score["tokens_per_point"] if score is not None else None
    return {
        "model": model,
        "trial": trial,
        "usd_per_million_tokens": price,
        "tokens_per_point": tokens_per_point,
        "usd_per_point": (
            None if tokens_per_point is None else round(tokens_per_point * price / 1_000_000, 6)
        ),
    }


def route(
    kind: str,
    runs: list[dict[str, Any]],
    closed: dict[str, set[int]],
    fuel: dict[str, Any],
    claude_price: float,
    emergency: bool,
    accounts: list[dict[str, Any]] | None = None,
    size: int | None = None,
) -> dict[str, Any]:
    """Choose the model for a *kind* run of *size* from its :func:`options`: the cheapest
    overflow model when it costs less than every Claude model and overflow is available,
    else the cheapest Claude model on the allowance, else the cheapest overflow model,
    else none.

    With registered *accounts*, Claude is available only through an account
    :func:`choose_account` picks, and the choice names it as ``account``."""
    ranked = options(kind, size, runs, closed, claude_price)
    allowance = [option for option in ranked if is_claude(option["model"])]
    overflow = [option for option in ranked if not is_claude(option["model"])]
    claude_available, overflow_available = fuel_available(fuel, emergency)
    account: dict[str, Any] | None = None
    held_by_pace = False
    if accounts:
        account, held_by_pace = choose_account(accounts, emergency)
        claude_available = account is not None
    work = f"{kind} at {size} points" if size is not None else kind

    def choose(option: dict[str, Any] | None, via: str | None, reason: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "size": size,
            "model": option["model"] if option is not None else None,
            "trial": option["trial"] if option is not None else False,
            "via": via,
            "account": account["name"] if account is not None and via == "allowance" else None,
            "reason": reason,
            "claude_available": claude_available,
            "overflow_available": overflow_available,
            "claude_usd_per_million_tokens": claude_price,
            "candidates": [option for option in ranked if option["model"] != CLAUDE],
        }

    def standing(option: dict[str, Any]) -> str:
        return f"{option['model']} is {'on trial' if option['trial'] else 'trusted'} for {work}"

    if overflow_available and overflow and ranked[0] is overflow[0]:
        return choose(overflow[0], "requesty", f"{standing(overflow[0])} and cheaper than Claude")
    if claude_available:
        best = allowance[0]
        if best["model"] == CLAUDE:
            reason = "Claude is trusted for every kind of work"
        else:
            reason = f"{standing(best)} and the cheapest on the allowance"
        if account is not None:
            reason += f"; account {account['name']} has the most allowance the run may use"
        return choose(best, "allowance", reason)
    if held_by_pace:
        return choose(
            None,
            None,
            "every Claude account with allowance left is ahead of its pace: wait until "
            "spending is back within the pace",
        )
    if overflow_available and overflow:
        return choose(
            overflow[0],
            "requesty",
            "the Claude allowance outside the reserve is spent or a shorter limit is in force; "
            "overflow through Requesty",
        )
    if overflow:
        return choose(
            None,
            None,
            "the Requesty cap is reached: ask a budget question, then continue with the fuel "
            "left or wait for the reset",
        )
    at_cap = (fuel.get("overflow") or {}).get("at_cap", False)
    if at_cap and not (fuel["reported"] and not fuel["current"]):
        return choose(
            None,
            None,
            f"no Claude account has allowance left for {work} and the Requesty cap is reached: "
            "ask a budget question, then wait for a reset",
        )
    return choose(
        None,
        None,
        f"no model trusted for {work} has fuel: wait for the Claude allowance to reset",
    )


async def route_run(
    kind: db.UsageKind, size: int | None = None, emergency: bool = False
) -> dict[str, Any]:
    """Return the model for a run of *kind* on an issue of *size* points, derived from the
    scorecard, the observed prices and the latest fuel report. With no *size*, only trust
    for the kind counts. *emergency* lets the run draw on the reserve."""
    if kind not in get_args(db.UsageKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(db.UsageKind))}, got {kind!r}")
    if size is not None:
        db.check_size(size)
    user_id = _require_user_id()
    runs = db.list_runs(user_id)
    closed = await closed_issues(runs)
    fuel = read(user_id, datetime.now(UTC))
    return route(
        kind,
        runs,
        closed,
        fuel,
        settings.claude_usd_per_million_tokens,
        emergency,
        fuel["accounts"],
        size,
    )
