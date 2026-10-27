"""Lachesis MCP tool implementations — fuel: what each run spent and what is left."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, get_args

import db

from .repos import _require_user_id
from .requirements import _resolve_repo

RESERVE_PERCENT = 10.0
DEFAULT_ACCOUNT = "factory"
_WEEK = timedelta(days=7)
_RESERVE_RELEASE = timedelta(hours=24)


async def report_usage(
    repo: str,
    issue: int,
    model: str,
    kind: db.UsageKind,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Record one run's usage on *issue* in *repo*.

    *cost_usd* is the money spent where the model is paid per token, and is left
    out for a run on the Claude allowance. *account* names the registered Claude
    account a run on an allowance drew on; left out, such a run is the primary
    account's. Returns the stored report.
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
    if account is not None:
        account = _registered(user_id, account)["name"]
    elif cost_usd is None:
        primary = _primary(user_id)
        account = primary["name"] if primary is not None else None
    repo = await _resolve_repo(repo, require_registered=True)
    return db.record_usage_report(
        user_id, repo, issue, model, kind, tokens_in, tokens_out, cost_usd, account
    )


def _primary(user_id: str) -> dict[str, Any] | None:
    return next((row for row in db.list_accounts(user_id) if row["primary"]), None)


def _registered(user_id: str, account: str) -> dict[str, Any]:
    """Return the registered account named *account*, or refuse naming the registered ones."""
    accounts = db.list_accounts(user_id)
    for row in accounts:
        if row["name"] == account.strip():
            return row
    known = ", ".join(row["name"] for row in accounts) or "none"
    raise ValueError(f"no registered account is named {account!r}; registered: {known}")


def _check_percent(name: str, value: float | None, *, allow_zero: bool) -> None:
    if value is None:
        return
    low_ok = value >= 0 if allow_zero else value > 0
    if not low_ok or value > 100:
        bound = "between 0 and 100" if allow_zero else "above 0 and at most 100"
        raise ValueError(f"{name} must be {bound}, got {value}")


async def register_account(
    name: str,
    share_percent: float,
    five_hour_pause_percent: float | None = None,
    primary: bool = False,
) -> dict[str, Any]:
    """Register a Claude account the factory may use, or update a registered one.

    *share_percent* is how much of its weekly allowance the factory may use, 100 for an
    account dedicated to the factory. While its five-hour window is at or above
    *five_hour_pause_percent*, no work is routed to it. The first account registered is
    primary, and *primary* makes this one the primary instead. Returns the account.
    """
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    _check_percent("share_percent", share_percent, allow_zero=False)
    _check_percent("five_hour_pause_percent", five_hour_pause_percent, allow_zero=False)
    return db.register_account(
        _require_user_id(), name, share_percent, five_hour_pause_percent, primary
    )


async def list_accounts() -> dict[str, Any]:
    """Return the registered accounts with their settings, the primary first."""
    return {"accounts": db.list_accounts(_require_user_id())}


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
    overflow_spent_usd: float | None = None,
    overflow_cap_usd: float | None = None,
    shorter_limit_until: str | None = None,
    account: str | None = None,
    five_hour_used_percent: float | None = None,
) -> dict[str, Any]:
    """Record an account's latest allowance status.

    *account* is the registered account reported on; left out, the primary account,
    which is registered as ``factory`` with a 100 percent share if no account is.
    Replaces that account's previous report only. *overflow_spent_usd* and
    *overflow_cap_usd* are accepted for older callers and ignored: only
    :func:`report_overflow` changes the overflow figures. Returns fuel as
    :func:`read_fuel` does.
    """
    _check_percent("weekly_used_percent", weekly_used_percent, allow_zero=True)
    _check_percent("five_hour_used_percent", five_hour_used_percent, allow_zero=True)
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
    if account is not None:
        name = _registered(user_id, account)["name"]
    else:
        primary = _primary(user_id) or db.register_account(
            user_id, DEFAULT_ACCOUNT, 100.0, None, True
        )
        name = primary["name"]
    db.set_fuel(
        user_id,
        name,
        weekly_used_percent,
        resets_at,
        limit_until,
        five_hour_used_percent,
    )
    return _read(user_id)


async def report_overflow(spent_usd: float, cap_usd: float) -> dict[str, Any]:
    """Record the money spent through Requesty this sprint and the cap the author set
    there, replacing the previous overflow report. Returns fuel as :func:`read_fuel` does.
    """
    if spent_usd < 0:
        raise ValueError(f"spent_usd must not be negative, got {spent_usd}")
    if cap_usd < 0:
        raise ValueError(f"cap_usd must not be negative, got {cap_usd}")
    user_id = _require_user_id()
    db.set_overflow(user_id, spent_usd, cap_usd)
    return _read(user_id)


def overflow_status(spent: float | None, cap: float | None) -> dict[str, Any]:
    """Return the overflow figures; until overflow is reported the cap is unknown."""
    if cap is None or spent is None:
        return {
            "reported": False,
            "spent_usd": None,
            "cap_usd": None,
            "left_usd": None,
            "at_cap": False,
        }
    return {
        "reported": True,
        "spent_usd": spent,
        "cap_usd": cap,
        "left_usd": round(max(0.0, cap - spent), 2),
        "at_cap": spent >= cap,
    }


def reserve_released(resets_at: datetime, now: datetime) -> bool:
    """Whether the reserve of a week resetting at *resets_at* is released for any work at
    *now*: in the last 24 hours before the reset, so none of it is lost there."""
    return now < resets_at <= now + _RESERVE_RELEASE


def fuel_status(row: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    """Return what fuel is left at *now*, from the primary account's latest fuel report *row*.

    The reported week is the 7 days up to ``weekly_resets_at``. The pace is the share
    of the allowance outside the reserve that may be used by the end of today when
    it is spread evenly over the week. In the last 24 hours before the reset the
    reserve is released, and the whole allowance is outside it.
    """
    if row is None:
        return {"reported": False, "reserve_percent": RESERVE_PERCENT}
    used = row["weekly_used_percent"]
    resets_at = row["weekly_resets_at"]
    limit_until = row["shorter_limit_until"]
    released = reserve_released(resets_at, now)
    outside_reserve = 100 - (0 if released else RESERVE_PERCENT)
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
            "reserve_released": released,
            "left_outside_reserve_percent": round(max(0.0, outside_reserve - used), 2),
            "only_reserve_left": used >= outside_reserve,
        },
        "overflow": overflow_status(row["overflow_spent_usd"], row["overflow_cap_usd"]),
        "pace": pace,
    }


def account_status(row: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Return what the factory may still use of one account at *now*, from a
    :func:`db.list_fuel` row: its reserve (the reserve percent of its share), what is
    usable outside the reserve and in an emergency, whether its five-hour window has
    paused it, and its pace over its own week. In the last 24 hours before its reset
    the reserve is released: usable for any work, and counted in its pace.

    An account never reported, or whose reported week has reset, counts as unused.
    """
    share = row["share_percent"]
    reserve = round(share * RESERVE_PERCENT / 100, 2)
    reported = row["reported_at"] is not None
    current = reported and now < row["weekly_resets_at"]
    released = current and reserve_released(row["weekly_resets_at"], now)
    held = 0.0 if released else reserve
    used = row["weekly_used_percent"] if current else 0.0
    limit_until = row["shorter_limit_until"] if reported else None
    limit_in_force = limit_until is not None and now < limit_until
    five_hour = row["five_hour_used_percent"] if reported else None
    pause_at = row["five_hour_pause_percent"]
    paused = pause_at is not None and five_hour is not None and five_hour >= pause_at
    pace = None
    if current:
        resets_at = row["weekly_resets_at"]
        day = min(max((now - (resets_at - _WEEK)) // timedelta(days=1) + 1, 1), 7)
        limit = round((share - held) * day / 7, 2)
        pace = {"day": day, "days": 7, "limit_percent": limit, "within_pace": used <= limit}
    return {
        "name": row["name"],
        "primary": row["primary"],
        "share_percent": share,
        "five_hour_pause_percent": pause_at,
        "reported": reported,
        "reported_at": row["reported_at"],
        "current": current,
        "used_percent": used,
        "resets_at": row["weekly_resets_at"] if reported else None,
        "shorter_limit_until": limit_until,
        "shorter_limit_in_force": limit_in_force,
        "five_hour_used_percent": five_hour,
        "paused": paused,
        "reserve_percent": reserve,
        "reserve_released": released,
        "usable_percent": round(max(0.0, share - held - used), 2),
        "emergency_usable_percent": round(max(0.0, share - used), 2),
        "only_reserve_left": used >= share - held,
        "pace": pace,
        "within_pace": pace["within_pace"] if pace else True,
    }


def accounts_status(user_id: str, now: datetime) -> list[dict[str, Any]]:
    """Return :func:`account_status` for every registered account, the primary first."""
    return [account_status(row, now) for row in db.list_fuel(user_id)]


def read(user_id: str, now: datetime) -> dict[str, Any]:
    """Return fuel at *now*: the primary account's figures at the top level, the overflow
    figures whoever reported, and every account's status."""
    overflow = db.get_overflow(user_id)
    return {
        **fuel_status(db.get_fuel(user_id), now),
        "overflow": overflow_status(
            overflow["spent_usd"] if overflow else None, overflow["cap_usd"] if overflow else None
        ),
        "accounts": accounts_status(user_id, now),
    }


def _read(user_id: str) -> dict[str, Any]:
    return read(user_id, datetime.now(UTC))


async def read_fuel() -> dict[str, Any]:
    """Return what fuel is left: every account's status with its reserve, pause and pace,
    the overflow spending against the cap as last reported, and, at the top level, the
    primary account's allowance and pace."""
    return _read(_require_user_id())
