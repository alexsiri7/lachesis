"""Lachesis MCP tool implementations — sprints: the week's budget, what fits in it, and the
report on each week that ended."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import db
import github
import notify
from settings import settings

from .fuel import RESERVE_PERCENT, accounts_status, fuel_status
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
    accounts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return the sprint holding *now* and its budget in tokens, from the primary
    account's fuel report *row*, whose week the sprint follows.

    The budget is what the factory may use of every account outside its reserve, plus
    the overflow cap converted at the Claude price. With *accounts*
    (:func:`accounts_status`) every account counts, each at its share less its reserve;
    without, the primary alone at its whole allowance. An account whose reported week
    has reset counts nothing spent. ``None`` when the primary account was never reported.
    """
    if row is None:
        return None
    fuel = fuel_status(row, now)
    start = _sprint_start(row["weekly_resets_at"], now)
    outside_reserve = 100 - RESERVE_PERCENT
    current = fuel["current"]
    used = row["weekly_used_percent"]
    cap = row["overflow_cap_usd"] or 0.0
    spent_usd = row["overflow_spent_usd"] or 0.0

    by_account: list[dict[str, Any]] = []
    if accounts:
        for account in accounts:
            usable = account["share_percent"] - account["reserve_percent"]
            account_used = min(account["used_percent"], usable)
            by_account.append(
                {
                    "name": account["name"],
                    "budget_tokens": round(allowance_tokens * usable / 100),
                    "spent_tokens": round(allowance_tokens * account_used / 100),
                }
            )
        allowance_budget = sum(a["budget_tokens"] for a in by_account)
        allowance_spent = sum(a["spent_tokens"] for a in by_account)
    else:
        allowance_budget = round(allowance_tokens * outside_reserve / 100)
        allowance_spent = (
            round(allowance_tokens * min(used, outside_reserve) / 100) if current else 0
        )
    overflow_budget = _usd_to_tokens(cap, usd_per_million)
    overflow_spent = _usd_to_tokens(min(spent_usd, cap), usd_per_million) if current else 0
    within_pace = fuel["pace"]["within_pace"] if fuel["pace"] else None
    if accounts:
        within_pace = any(
            a["within_pace"] and a["usable_percent"] > 0 and not a["paused"] for a in accounts
        )

    budget = allowance_budget + overflow_budget
    spent = allowance_spent + overflow_spent
    return {
        "start": start,
        "end": start + _SPRINT,
        "budget_tokens": budget,
        "spent_tokens": spent,
        "left_tokens": max(0, budget - spent),
        "within_pace": within_pace,
        "allowance": {
            "weekly_tokens": allowance_tokens,
            "reserve_percent": RESERVE_PERCENT,
            "used_percent": used,
            "budget_tokens": allowance_budget,
            "spent_tokens": allowance_spent,
            "accounts": by_account,
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
        accounts_status(user_id, now),
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


ESTIMATE_MISS_FACTOR = 2


def ended_sprint(row: dict[str, Any] | None, now: datetime) -> tuple[datetime, datetime] | None:
    """Return the start and end of the sprint before the one holding *now*.

    ``None`` when fuel was never reported, so no sprint has ended.
    """
    if row is None:
        return None
    start = _sprint_start(row["weekly_resets_at"], now) - _SPRINT
    return start, start + _SPRINT


def _tokens(run: dict[str, Any]) -> int:
    return int(run["tokens_in"]) + int(run["tokens_out"])


def sprint_spending(
    runs: list[dict[str, Any]], row: dict[str, Any] | None, end: datetime
) -> dict[str, Any]:
    """Return what the sprint's *runs* spent on Claude and on overflow.

    A run with no cost was on the Claude allowance. The fuel report *row* adds the
    allowance used and the overflow spent against the cap only while it still
    describes the week ending at *end*: the next week's report replaces it.
    """
    claude = [run for run in runs if run["cost_usd"] is None]
    overflow = [run for run in runs if run["cost_usd"] is not None]
    fuel = row if row is not None and row["weekly_resets_at"] == end else None
    by_account: dict[str, dict[str, int]] = {}
    for run in claude:
        entry = by_account.setdefault(run.get("account") or "unnamed", {"runs": 0, "tokens": 0})
        entry["runs"] += 1
        entry["tokens"] += _tokens(run)
    return {
        "claude": {
            "runs": len(claude),
            "tokens": sum(_tokens(run) for run in claude),
            "allowance_used_percent": fuel["weekly_used_percent"] if fuel else None,
            "accounts": [{"name": name, **by_account[name]} for name in sorted(by_account)],
        },
        "overflow": {
            "runs": len(overflow),
            "tokens": sum(_tokens(run) for run in overflow),
            "cost_usd": round(sum(run["cost_usd"] for run in overflow), 2),
            "spent_usd": fuel["overflow_spent_usd"] if fuel else None,
            "cap_usd": fuel["overflow_cap_usd"] if fuel else None,
        },
    }


def estimate_accuracy(
    closed: list[tuple[str, int]],
    estimates: list[dict[str, Any]],
    usage: dict[str, dict[int, dict[str, int]]],
) -> dict[str, Any]:
    """Compare the estimate of each *closed* issue with its actual usage.

    Only an issue with a positive estimate and some reported usage counts. Returns
    each issue and the totals by kind and capability; a ratio is actual over estimate.
    """
    by_issue = {(normalize_repo(row["repo_name"]), row["issue"]): row for row in estimates}
    issues: list[dict[str, Any]] = []
    for repo, number in closed:
        estimate = by_issue.get((repo, number))
        actual = sum(usage.get(repo, {}).get(number, {}).values())
        if estimate is None or not estimate["tokens"] or actual <= 0:
            continue
        issues.append(
            {
                "repo": repo,
                "number": number,
                "kind": estimate["kind"],
                "capability": estimate["capability"],
                "estimate_tokens": estimate["tokens"],
                "actual_tokens": actual,
                "ratio": round(actual / estimate["tokens"], 2),
            }
        )

    groups: dict[tuple[str, str | None], dict[str, Any]] = {}
    for issue in issues:
        group = groups.setdefault(
            (issue["kind"], issue["capability"]),
            {
                "kind": issue["kind"],
                "capability": issue["capability"],
                "issues": 0,
                "estimate_tokens": 0,
                "actual_tokens": 0,
            },
        )
        group["issues"] += 1
        group["estimate_tokens"] += issue["estimate_tokens"]
        group["actual_tokens"] += issue["actual_tokens"]
    by_kind_and_capability = [
        {**group, "ratio": round(group["actual_tokens"] / group["estimate_tokens"], 2)}
        for _, group in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or ""))
    ]
    return {"issues": issues, "by_kind_and_capability": by_kind_and_capability}


def _is_estimate_miss(estimate: int, actual: int) -> bool:
    return actual > ESTIMATE_MISS_FACTOR * estimate or ESTIMATE_MISS_FACTOR * actual < estimate


def _miss_sentence(miss: dict[str, Any]) -> str:
    if miss["kind"] == "estimate":
        return (
            f"estimated {miss['estimate_tokens']} tokens of {miss['work']} work in "
            f"{miss['capability'] or 'no capability'}, used {miss['actual_tokens']} "
            f"({miss['ratio']:.2f}x)."
        )
    if miss["kind"] == "retry":
        return f"{miss['count']} {'retry' if miss['count'] == 1 else 'retries'}."
    return f"{miss['count']} rework."


def sprint_findings(
    start: datetime,
    end: datetime,
    accuracy_issues: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one finding for each issue that missed during the sprint, by repo and issue.

    A miss is an estimate off by more than ``ESTIMATE_MISS_FACTOR`` either way, or a
    retry or rework on a run of the sprint.
    """
    misses: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for issue in accuracy_issues:
        if _is_estimate_miss(issue["estimate_tokens"], issue["actual_tokens"]):
            misses.setdefault((issue["repo"], issue["number"]), []).append(
                {
                    "kind": "estimate",
                    "work": issue["kind"],
                    "capability": issue["capability"],
                    "estimate_tokens": issue["estimate_tokens"],
                    "actual_tokens": issue["actual_tokens"],
                    "ratio": issue["ratio"],
                }
            )
    failures: dict[tuple[str, int], dict[str, int]] = {}
    for run in runs:
        counts = failures.setdefault(
            (normalize_repo(run["repo_name"]), run["issue"]), {"retry": 0, "rework": 0}
        )
        counts["retry"] += run["retries"]
        counts["rework"] += run["rework"]
    for key, counts in failures.items():
        for kind, count in counts.items():
            if count:
                misses.setdefault(key, []).append({"kind": kind, "count": count})

    prefix = f"Sprint {start.date().isoformat()} to {end.date().isoformat()} review:"
    return [
        {
            "repo": repo,
            "issue": issue,
            "misses": found,
            "text": " ".join([prefix, *(_miss_sentence(miss) for miss in found)]),
        }
        for (repo, issue), found in sorted(misses.items())
    ]


def _push_body(report: dict[str, Any]) -> str:
    delivered = report["delivered"]
    claude = report["spending"]["claude"]
    overflow = report["spending"]["overflow"]
    estimated = report["estimates"]["issues"]
    if estimated:
        ratio = sum(i["actual_tokens"] for i in estimated) / sum(
            i["estimate_tokens"] for i in estimated
        )
        estimates = f"Estimates: actual {ratio:.2f}x estimated over {len(estimated)} issues."
    else:
        estimates = "Estimates: no estimated issue closed."
    return "\n".join(
        [
            f"Delivered: {len(delivered['milestones'])} milestones, "
            f"{len(delivered['issues'])} issues.",
            f"Spent: {claude['tokens']} Claude tokens"
            + "".join(f" ({a['name']}: {a['tokens']})" for a in claude.get("accounts", []))
            + f"; {overflow['tokens']} overflow tokens, ${overflow['cost_usd']:.2f}.",
            estimates,
            f"Carried over: {len(report['carryover'])} issues.",
            f"Findings: {len(report['findings'])}.",
        ]
    )


def _in_sprint(stamp: str | None, start: datetime, end: datetime) -> bool:
    return stamp is not None and start <= datetime.fromisoformat(stamp) < end


def _issue_ref(repo: str, issue: dict[str, Any]) -> dict[str, Any]:
    return {"repo": repo, "number": issue["number"], "title": issue["title"], "url": issue["url"]}


async def report_sprint() -> dict[str, Any]:
    """Report the sprint that last ended, once.

    Storing the report claims the sprint before the finding notes and the push
    notification, so a report that fails after it drops them rather than repeating
    them. Every later call returns the stored report.
    """
    user_id = _require_user_id()
    fuel = db.get_fuel(user_id)
    sprint = ended_sprint(fuel, datetime.now(UTC))
    if sprint is None:
        return {
            "reported": False,
            "explanation": "No fuel has been reported, so no sprint has ended yet — call "
            "report_fuel.",
        }
    start, end = sprint
    stored = db.get_sprint_report(user_id, end)
    if stored is not None:
        return {"reported": True, "first_report": False, **stored["report"]}

    runs = db.list_runs(user_id, start=start, end=end)
    worked_on = {(normalize_repo(run["repo_name"]), run["issue"]) for run in runs}
    delivered: list[dict[str, Any]] = []
    carryover: list[dict[str, Any]] = []
    usage: dict[str, dict[int, dict[str, int]]] = {}
    unreadable: list[str] = []
    for repo in [normalize_repo(row["repo_name"]) for row in db.list_repos(user_id)]:
        try:
            listing = await github.list_all_issues(repo)
        except Exception:
            logger.warning("report_sprint: could not list the issues of %s", repo)
            unreadable.append(repo)
            continue
        usage[repo] = db.usage_by_issue(user_id, repo)
        for issue in listing:
            if issue["is_pull_request"]:
                continue
            if issue["state"] == "open" and (repo, issue["number"]) in worked_on:
                carryover.append(_issue_ref(repo, issue))
            elif (
                issue["state"] == "closed"
                and issue["state_reason"] != "not_planned"
                and _in_sprint(issue["closed_at"], start, end)
            ):
                delivered.append(_issue_ref(repo, issue))
    carryover.sort(key=lambda ref: (ref["repo"], ref["number"]))
    delivered.sort(key=lambda ref: (ref["repo"], ref["number"]))

    accuracy = estimate_accuracy(
        [(ref["repo"], ref["number"]) for ref in delivered], db.list_estimates(user_id), usage
    )
    report = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "delivered": {
            "milestones": [
                {"repo": normalize_repo(m["repo_name"]), "id": m["id"], "title": m["title"]}
                for m in db.milestones_done_between(user_id, start, end)
            ],
            "issues": delivered,
        },
        "spending": sprint_spending(runs, fuel, end),
        "estimates": accuracy,
        "carryover": carryover,
        "findings": sprint_findings(start, end, accuracy["issues"], runs),
        "unreadable": unreadable,
    }

    if db.claim_sprint_report(user_id, start, end, report) is None:
        stored = db.get_sprint_report(user_id, end)
        return {
            "reported": True,
            "first_report": False,
            **(stored["report"] if stored is not None else report),
        }
    for finding in report["findings"]:
        try:
            db.record_note(
                user_id,
                finding["repo"],
                "lachesis",
                "finding",
                finding["text"],
                issue=finding["issue"],
            )
        except Exception:
            logger.warning(
                "report_sprint: could not note the finding on %s#%s",
                finding["repo"],
                finding["issue"],
                exc_info=True,
            )
    await notify.send(
        user_id,
        title=f"Sprint report: {start.date().isoformat()} to {end.date().isoformat()}",
        body=_push_body(report),
        tags=["bar_chart"],
    )
    return {"reported": True, "first_report": True, **report}
