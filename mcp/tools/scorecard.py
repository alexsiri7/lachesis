"""Lachesis MCP tool implementations — scorecard: how each model does at each kind of work."""

from __future__ import annotations

import asyncio
from typing import Any, get_args

import db
import github

from .repos import _require_user_id
from .requirements import _resolve_repo

TRUST_AFTER_CLEAN_RUNS = 5
TRUST_WINDOW = 5
TRUST_LOST_AFTER_FAILURES = 2
TRIAL_KINDS = ("triage", "summarising")


def trust_level(judged: list[tuple[int, bool]]) -> int | None:
    """Replay *judged* runs in order, each its size and True for a clean run, and return
    the largest size trust stands at, or None.

    Trust at a size comes after TRUST_AFTER_CLEAN_RUNS clean runs in a row at that size or
    larger, and covers every smaller size. It is lost at every size once
    TRUST_LOST_AFTER_FAILURES of the last TRUST_WINDOW runs at the sizes it covered failed.
    """
    level: int | None = None
    streaks = dict.fromkeys(db.SIZES, 0)
    recent: list[bool] = []
    for size, clean in judged:
        for at_least in db.SIZES:
            if size >= at_least:
                streaks[at_least] = streaks[at_least] + 1 if clean else 0
        if level is not None and size <= level:
            recent = (recent + [clean])[-TRUST_WINDOW:]
            if recent.count(False) >= TRUST_LOST_AFTER_FAILURES:
                level = None
                streaks = dict.fromkeys(db.SIZES, 0)
                recent = []
        earned = [at_least for at_least in db.SIZES if streaks[at_least] >= TRUST_AFTER_CLEAN_RUNS]
        if earned and (level is None or earned[-1] > level):
            level = earned[-1]
    return level


def _judge(runs: list[dict[str, Any]], closed: dict[str, set[int]]) -> list[tuple[int, bool]]:
    """Return *runs* judged in order, each its size and True for clean: merged with no
    failure reported, or failed once any is. Runs on open issues with no failure are not
    yet judged; a run on an issue never sized counts at the smallest size."""
    judged: list[tuple[int, bool]] = []
    for run in runs:
        merged = run["issue"] in closed.get(run["repo_name"], set())
        failed = run["rework"] + run["retries"] + run["audit_findings"] > 0
        if failed or merged:
            judged.append((run["size"] or db.SIZES[0], not failed))
    return judged


def _totals(runs: list[dict[str, Any]], closed: dict[str, set[int]]) -> dict[str, Any]:
    return {
        "runs": len(runs),
        "merged_without_rework": sum(
            1
            for run in runs
            if run["issue"] in closed.get(run["repo_name"], set()) and not run["rework"]
        ),
        "retries": sum(run["retries"] for run in runs),
        "audit_findings": sum(run["audit_findings"] for run in runs),
        "tokens_in": sum(run["tokens_in"] for run in runs),
        "tokens_out": sum(run["tokens_out"] for run in runs),
        "cost_usd": round(sum(run["cost_usd"] or 0.0 for run in runs), 2),
    }


def _per_point(sized: list[dict[str, Any]], closed: dict[str, set[int]]) -> dict[str, Any]:
    """Tokens and cost of the *sized* runs per point delivered: the sizes of their issues
    that merged, each issue counted once. None while none merged."""
    delivered = {
        (run["repo_name"], run["issue"]): run["size"]
        for run in sized
        if run["issue"] in closed.get(run["repo_name"], set())
    }
    points = sum(delivered.values())
    tokens = sum(run["tokens_in"] + run["tokens_out"] for run in sized)
    cost = sum(run["cost_usd"] or 0.0 for run in sized)
    return {
        "tokens_per_point": round(tokens / points, 2) if points else None,
        "cost_per_point": round(cost / points, 4) if points else None,
    }


def _kind_score(runs: list[dict[str, Any]], closed: dict[str, set[int]]) -> dict[str, Any]:
    sized = [run for run in runs if run["size"] is not None]
    up_to = trust_level(_judge(runs, closed))
    sizes = []
    for size in sorted({run["size"] for run in sized}):
        at_size = [run for run in sized if run["size"] == size]
        totals = _totals(at_size, closed)
        sizes.append(
            {
                "size": size,
                "runs": totals["runs"],
                "merged_without_rework": totals["merged_without_rework"],
                "retries": totals["retries"],
                "audit_findings": totals["audit_findings"],
                "tokens": totals["tokens_in"] + totals["tokens_out"],
                "cost_usd": totals["cost_usd"],
                **_per_point(at_size, closed),
                "trusted": up_to is not None and size <= up_to,
            }
        )
    return {
        **_totals(runs, closed),
        **_per_point(sized, closed),
        "trusted": up_to is not None,
        "trusted_up_to": up_to,
        "sizes": sizes,
    }


def scorecard(runs: list[dict[str, Any]], closed: dict[str, set[int]]) -> list[dict[str, Any]]:
    """Score each model with *runs* at every kind of work and every issue size, a run being
    merged once its issue is in *closed* for its repository.

    A run is judged clean once merged with no failure reported, failed once any is; runs
    on open issues with no failure are not yet judged. A model is trusted for a kind when
    it is trusted at some size. A model may trial the trial kinds, any kind it is trusted
    for, and any kind after one it is trusted for.
    """
    kinds: tuple[str, ...] = get_args(db.UsageKind)
    by_model: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for run in runs:
        by_model.setdefault(run["model"], {}).setdefault(run["kind"], []).append(run)
    models = []
    for model in sorted(by_model):
        scores = []
        trusted_before = False
        for kind in kinds:
            score = _kind_score(by_model[model].get(kind, []), closed)
            score["may_trial"] = kind in TRIAL_KINDS or score["trusted"] or trusted_before
            trusted_before = trusted_before or score["trusted"]
            scores.append({"kind": kind, **score})
        models.append({"model": model, "kinds": scores})
    return models


async def report_run_outcome(
    repo: str, issue: int, kind: db.UsageKind, outcome: db.RunOutcome
) -> dict[str, Any]:
    """Report a failure of the latest *kind* run on *issue* in *repo*.

    *outcome* is ``rework`` when review sent the work back, ``retry`` when the run failed
    and is being retried, and ``audit_finding`` when a later audit found a gap in it.
    """
    if issue < 1:
        raise ValueError(f"a run outcome must name an issue, got {issue}")
    if kind not in get_args(db.UsageKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(db.UsageKind))}, got {kind!r}")
    if outcome not in get_args(db.RunOutcome):
        raise ValueError(
            f"outcome must be one of {', '.join(get_args(db.RunOutcome))}, got {outcome!r}"
        )
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    return db.record_run_outcome(user_id, repo, issue, kind, outcome)


async def closed_issues(runs: list[dict[str, Any]]) -> dict[str, set[int]]:
    """Return the closed issue numbers of every repository with *runs*, read from GitHub."""
    repos = sorted({run["repo_name"] for run in runs})
    listings = await asyncio.gather(*(github.list_all_issues(repo) for repo in repos))
    return {
        repo: {
            item["number"]
            for item in issues
            if not item["is_pull_request"] and item["state"] == "closed"
        }
        for repo, issues in zip(repos, listings, strict=True)
    }


async def read_scorecard() -> dict[str, Any]:
    """Return the scorecard of every model with runs, derived from the runs and GitHub."""
    runs = db.list_runs(_require_user_id())
    return {"models": scorecard(runs, await closed_issues(runs))}
