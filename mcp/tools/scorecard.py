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


def is_trusted(judged: list[bool]) -> bool:
    """Replay *judged* runs in order, True for a clean run, and return whether trust stands.

    Trust comes after TRUST_AFTER_CLEAN_RUNS clean runs in a row and is lost once
    TRUST_LOST_AFTER_FAILURES of the last TRUST_WINDOW runs failed.
    """
    trusted = False
    streak = 0
    recent: list[bool] = []
    for clean in judged:
        recent = (recent + [clean])[-TRUST_WINDOW:]
        if trusted:
            if recent.count(False) >= TRUST_LOST_AFTER_FAILURES:
                trusted = False
                streak = 0
        else:
            streak = streak + 1 if clean else 0
            trusted = streak >= TRUST_AFTER_CLEAN_RUNS
    return trusted


def _kind_score(runs: list[dict[str, Any]], closed: dict[str, set[int]]) -> dict[str, Any]:
    judged: list[bool] = []
    merged_without_rework = 0
    for run in runs:
        merged = run["issue"] in closed.get(run["repo_name"], set())
        failed = run["rework"] + run["retries"] + run["audit_findings"] > 0
        if merged and not run["rework"]:
            merged_without_rework += 1
        if failed or merged:
            judged.append(not failed)
    return {
        "runs": len(runs),
        "merged_without_rework": merged_without_rework,
        "retries": sum(run["retries"] for run in runs),
        "audit_findings": sum(run["audit_findings"] for run in runs),
        "tokens_in": sum(run["tokens_in"] for run in runs),
        "tokens_out": sum(run["tokens_out"] for run in runs),
        "cost_usd": round(sum(run["cost_usd"] or 0.0 for run in runs), 2),
        "trusted": is_trusted(judged),
    }


def scorecard(runs: list[dict[str, Any]], closed: dict[str, set[int]]) -> list[dict[str, Any]]:
    """Score each model with *runs* at every kind of work, a run being merged once its
    issue is in *closed* for its repository.

    A run is judged clean once merged with no failure reported, failed once any is; runs
    on open issues with no failure are not yet judged. A model may trial the trial kinds,
    any kind it is trusted for, and any kind after one it is trusted for.
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
