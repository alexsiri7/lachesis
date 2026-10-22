"""Lachesis MCP tool implementations — sprints: the week's budget, what fits in it, and the
report on each week that ended."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

import db
import github
import notify
from settings import settings

from . import routing
from .audits import audit_history
from .estimates import Price, _capability, factory_history, price_per_point
from .experiments import experiment_view
from .fuel import RESERVE_PERCENT, accounts_status, fuel_status, read
from .repos import _require_user_id, normalize_repo
from .requirements import _NEEDS_AUTHOR_LABEL, _SPRINT, _backlog_order, _sprint_start
from .scorecard import closed_issues

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


def current_budget(user_id: str, now: datetime) -> dict[str, Any] | None:
    """Return :func:`sprint_budget` for the sprint holding *now*, from the stored fuel."""
    return sprint_budget(
        db.get_fuel(user_id),
        now,
        settings.claude_weekly_allowance_tokens,
        settings.claude_usd_per_million_tokens,
        accounts_status(user_id, now),
    )


def trial_share(
    user_id: str, budget: dict[str, Any], runs: list[dict[str, Any]], closed: dict[str, set[int]]
) -> dict[str, int]:
    """Return the trial share of the sprint with *budget*: TRIAL_SHARE_PERCENT of it, what
    the sprint's trials among *runs* spent of it, and what is left."""
    share = round(budget["budget_tokens"] * settings.trial_share_percent / 100)
    in_sprint = db.list_runs(user_id, start=budget["start"], end=budget["end"])
    spent = routing.trial_tokens(runs, closed, {run["id"] for run in in_sprint})
    return {"budget_tokens": share, "spent_tokens": spent, "left_tokens": max(0, share - spent)}


def estimates_by_issue(rows: list[dict[str, Any]]) -> dict[tuple[str, int], int | None]:
    """Return each estimated issue's tokens in the estimate *rows*, keyed by
    ``(repo, issue)``."""
    return {(normalize_repo(row["repo_name"]), row["issue"]): row["tokens"] for row in rows}


def fits(estimate: int | None, left_tokens: int) -> bool:
    """Whether work estimated at *estimate* fits *left_tokens*.

    An issue with no estimate fits while any budget is left: with no history every
    estimate is missing, and leaving them out would stall all work.
    """
    return left_tokens > 0 and (estimate or 0) <= left_tokens


def capacity_points(budget_tokens: int, price: Price) -> int | None:
    """Return how many whole points *budget_tokens* buys at *price*; none with no price."""
    if not price["tokens_per_point"]:
        return None
    return math.floor(budget_tokens / price["tokens_per_point"])


def plan_sprint(
    entries: list[dict[str, Any]],
    estimates: dict[tuple[str, int], int | None],
    left_tokens: int,
    sizes: dict[tuple[str, int], int] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Walk *entries* in backlog order, planning each whose estimate fits what is left.

    Parked work and work waiting on the author are not eligible. An entry that does
    not fit is skipped and the walk goes on, so smaller later work uses what is left.
    An issue sized larger than 13 is never planned itself: splitting it is, marked
    ``split``. Returns the planned entries and how many eligible entries were not planned.
    """
    sizes = sizes or {}
    planned: list[dict[str, Any]] = []
    not_planned = 0
    for entry in entries:
        if not entry["ready"] or _NEEDS_AUTHOR_LABEL in entry["labels"]:
            continue
        size = sizes.get((entry["repo"], entry["number"]))
        if size == db.TOO_BIG:
            planned.append(
                {
                    "repo": entry["repo"],
                    "number": entry["number"],
                    "title": entry["title"],
                    "tier": entry["tier"],
                    "size": size,
                    "estimate_tokens": None,
                    "split": True,
                }
            )
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
                "size": size,
                "estimate_tokens": estimate,
            }
        )
    return planned, not_planned


def plan_audits(due: list[dict[str, Any]], left_tokens: int) -> tuple[list[dict[str, Any]], int]:
    """Walk the *due* audits in order, planning each whose estimate fits what is left, as
    :func:`plan_sprint` does issues. A planned audit names its ``audit`` kind.

    Unlike an issue, an audit with no estimate is not eligible: no run of its kind has
    measured what it costs. next_issue still offers it, so that its first run can.
    """
    planned: list[dict[str, Any]] = []
    not_planned = 0
    for audit in due:
        estimate = audit["estimate_tokens"]
        if estimate is None:
            continue
        if not fits(estimate, left_tokens):
            not_planned += 1
            continue
        left_tokens -= estimate
        planned.append(
            {
                "repo": audit["repo"],
                "audit": audit["kind"],
                "capability": audit["capability"],
                "estimate_tokens": estimate,
                "confidence": audit["confidence"],
            }
        )
    return planned, not_planned


def _planned_tokens(plan: list[dict[str, Any]]) -> int:
    return sum(item["estimate_tokens"] or 0 for item in plan)


def plan_with_improvement(
    entries: list[dict[str, Any]],
    improvement_issues: set[tuple[str, int]],
    estimates: dict[tuple[str, int], int | None],
    left_tokens: int,
    improvement_tokens: int,
    sizes: dict[tuple[str, int], int] | None = None,
    due_audits: list[dict[str, Any]] | None = None,
    trial_issues: set[tuple[str, int]] | None = None,
    trial_tokens: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Plan the *trial_issues* among *entries* first, within *trial_tokens* of what is
    left, marked ``trial``; then the *improvement_issues*, within *improvement_tokens*,
    marked ``improvement``; then the *due_audits*, which the factory draws alongside
    issues rather than after them; then the ordinary backlog in what remains.

    A trial counts against the trial share even when it is improvement work too. One
    that does not fit the share is planned as the work it is, for a trusted model, as
    route_run routes it once trials have spent the share. An improvement issue that does
    not fit its share waits for the next sprint rather than joining the ordinary backlog.
    Returns the plan and how many eligible entries were not planned, as
    :func:`plan_sprint` does.
    """
    trial_issues = trial_issues or set()
    trials, _ = plan_sprint(
        [e for e in entries if (e["repo"], e["number"]) in trial_issues],
        estimates,
        min(trial_tokens, left_tokens),
        sizes,
    )
    left_tokens -= _planned_tokens(trials)
    on_trial = {(item["repo"], item["number"]) for item in trials}
    rest = [e for e in entries if (e["repo"], e["number"]) not in on_trial]
    improvement, improvement_not_planned = plan_sprint(
        [e for e in rest if (e["repo"], e["number"]) in improvement_issues],
        estimates,
        min(improvement_tokens, left_tokens),
        sizes,
    )
    left_tokens -= _planned_tokens(improvement)
    audits, audits_not_planned = plan_audits(due_audits or [], left_tokens)
    ordinary, ordinary_not_planned = plan_sprint(
        [e for e in rest if (e["repo"], e["number"]) not in improvement_issues],
        estimates,
        left_tokens - _planned_tokens(audits),
        sizes,
    )
    return (
        [
            {**item, "trial": True}
            | (
                {"improvement": True}
                if (item["repo"], item["number"]) in improvement_issues
                else {}
            )
            for item in trials
        ]
        + [{**item, "improvement": True} for item in improvement]
        + audits
        + ordinary,
        improvement_not_planned + audits_not_planned + ordinary_not_planned,
    )


def trial_issues(
    estimates: list[dict[str, Any]],
    sizes: dict[tuple[str, int], int],
    runs: list[dict[str, Any]],
    closed: dict[str, set[int]],
    fuel: dict[str, Any],
    claude_price: float,
) -> set[tuple[str, int]]:
    """Return the estimated issues whose next run, of their estimate's kind at their
    size, :func:`routing.route` would give a model on trial."""
    routed: dict[tuple[str, int | None], bool] = {}
    found: set[tuple[str, int]] = set()
    for row in estimates:
        key = (normalize_repo(row["repo_name"]), row["issue"])
        size = sizes.get(key)
        if size == db.TOO_BIG:
            continue
        if (row["kind"], size) not in routed:
            choice = routing.route(
                row["kind"], runs, closed, fuel, claude_price, False, fuel["accounts"], size
            )
            routed[(row["kind"], size)] = choice["trial"]
        if routed[(row["kind"], size)]:
            found.add(key)
    return found


async def read_sprint() -> dict[str, Any]:
    """Return the current sprint: its dates, budget and the shares of it kept for trials
    and for improvement work, spending, the price of a point and the capacity in points it
    gives, and the plan of issues and due audits."""
    # The backlogs, the audits and the factory history read the same whole listings.
    with github.sharing_listings():
        return await _read_sprint()


async def _read_sprint() -> dict[str, Any]:
    user_id = _require_user_id()
    now = datetime.now(UTC)
    budget = current_budget(user_id, now)
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

    sizes = {
        (normalize_repo(repo), issue): size
        for (repo, issue), size in db.sizes_by_issue(user_id).items()
    }
    improvement_tokens = budget["budget_tokens"] * settings.improvement_share_percent // 100
    audits = await audit_history(user_id)
    estimates = db.list_estimates(user_id)
    runs = db.list_runs(user_id)
    closed = await closed_issues(runs)
    trials = trial_share(user_id, budget, runs, closed)
    plan, not_planned = plan_with_improvement(
        [entry for _, _, entry in entries],
        {
            (normalize_repo(experiment["repo_name"]), issue)
            for experiment in db.list_open_experiments(user_id)
            for issue in experiment["issues"]
        },
        estimates_by_issue(estimates),
        budget["left_tokens"],
        improvement_tokens,
        sizes,
        audits["due"],
        trial_issues(
            estimates,
            sizes,
            runs,
            closed,
            read(user_id, now),
            settings.claude_usd_per_million_tokens,
        ),
        trials["left_tokens"],
    )
    history, unlisted = await factory_history(user_id)
    price = price_per_point(history)
    return {
        "reported": True,
        **budget,
        "improvement_budget_tokens": improvement_tokens,
        "trial_budget_tokens": trials["budget_tokens"],
        "trial_spent_tokens": trials["spent_tokens"],
        "tokens_per_point": price["tokens_per_point"],
        "price_confidence": price["confidence"],
        "capacity_points": capacity_points(budget["budget_tokens"], price),
        "plan": plan,
        "planned_tokens": _planned_tokens(plan),
        "planned_points": sum(item.get("size") or 0 for item in plan if not item.get("split")),
        "not_planned": not_planned,
        "unreadable": list(dict.fromkeys([*unreadable, *unlisted, *audits["unreadable"]])),
    }


ESTIMATE_MISS_FACTOR = 2
REPEAT_THRESHOLD = 3


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


def repeated_failures(
    runs: list[dict[str, Any]], capabilities: dict[tuple[str, int], str | None]
) -> list[dict[str, Any]]:
    """Return each failure repeated in the sprint: a retry or rework on the runs of at
    least ``REPEAT_THRESHOLD`` issues of one capability, by repo, outcome and capability.

    An issue missing from *capabilities*, or naming no single capability, repeats nothing.
    """
    struck: dict[tuple[str, str, str], set[int]] = {}
    for run in runs:
        key = (normalize_repo(run["repo_name"]), run["issue"])
        capability = capabilities.get(key)
        if capability is None:
            continue
        for outcome, count in (("retry", run["retries"]), ("rework", run["rework"])):
            if count:
                struck.setdefault((key[0], outcome, capability), set()).add(run["issue"])
    return [
        {"repo": repo, "outcome": outcome, "capability": capability, "issues": sorted(issues)}
        for (repo, outcome, capability), issues in sorted(struck.items())
        if len(issues) >= REPEAT_THRESHOLD
    ]


def _stop_the_line(user_id: str, repeat: dict[str, Any]) -> None:
    """Open the experiment removing *repeat*'s cause, unless one is open already."""
    title = f"Stop the line: {repeat['outcome']} in {repeat['capability']}"
    if any(
        normalize_repo(row["repo_name"]) == repeat["repo"] and row["title"] == title
        for row in db.list_open_experiments(user_id)
    ):
        return
    issues = ", ".join(f"#{number}" for number in repeat["issues"])
    db.create_experiment(
        user_id,
        repeat["repo"],
        title,
        f"Find and remove the cause of the {repeat['outcome']} that struck {issues} "
        f"in {repeat['capability']}.",
        f"issues of {repeat['capability']} with a {repeat['outcome']} in a sprint",
        len(repeat["issues"]),
        0,
        repeat["issues"],
    )


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
            f"Audits: {len(report['audits'])}, filing "
            f"{sum(len(audit['issues']) for audit in report['audits'])} findings.",
            f"Carried over: {len(report['carryover'])} issues.",
            f"Findings: {len(report['findings'])}.",
        ]
    )


def sprint_audits(
    runs: list[dict[str, Any]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Return the audit *runs* that finished during the sprint, oldest first, each with
    its outcome and the issues it filed."""
    return [
        {
            "repo": normalize_repo(run["repo_name"]),
            "kind": run["kind"],
            "capability": run["capability"],
            "finished_at": run["finished_at"].isoformat(),
            "outcome": run["outcome"],
            "model": run["model"],
            "issues": run["issues"],
            "tokens": _tokens(run),
        }
        for run in runs
        if start <= run["finished_at"] < end
    ]


def _in_sprint(stamp: str | None, start: datetime, end: datetime) -> bool:
    return stamp is not None and start <= datetime.fromisoformat(stamp) < end


def _issue_ref(repo: str, issue: dict[str, Any]) -> dict[str, Any]:
    return {"repo": repo, "number": issue["number"], "title": issue["title"], "url": issue["url"]}


async def report_sprint() -> dict[str, Any]:
    """Report the sprint that last ended, once.

    Storing the report claims the sprint before the finding notes and the push
    notification, so a report that fails after it drops them rather than repeating
    them. Every later call returns the stored report. The experiments stopping the line
    are opened before the claim so that the report lists them; a retried report finds
    them open and opens none again.
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
    capabilities: dict[tuple[str, int], str | None] = {}
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
            if (repo, issue["number"]) in worked_on:
                capabilities[(repo, issue["number"])] = _capability(issue["body"])
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
    stop_the_line = repeated_failures(runs, capabilities)
    for repeat in stop_the_line:
        _stop_the_line(user_id, repeat)
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
        "audits": sprint_audits(db.list_audit_runs(user_id), start, end),
        "findings": sprint_findings(start, end, accuracy["issues"], runs),
        "stop_the_line": stop_the_line,
        "experiments": [experiment_view(row) for row in db.list_open_experiments(user_id)],
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
