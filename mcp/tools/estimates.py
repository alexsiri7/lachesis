"""Lachesis MCP tool implementations — estimates: what an issue should cost, from history."""

from __future__ import annotations

from statistics import mean
from typing import Any, TypedDict, get_args

import db
import github
from spec_reference import parse_reference

from .repos import _require_user_id
from .requirements import _resolve_repo

NORMAL_CONFIDENCE_SAMPLES = 5


class Estimate(TypedDict):
    tokens: int | None
    confidence: db.EstimateConfidence
    basis: db.EstimateBasis
    samples: int


def issue_kind(usage: dict[str, int]) -> str:
    """Return the kind of work that used the most tokens in *usage*, ties in kind order."""
    kinds: tuple[str, ...] = get_args(db.UsageKind)
    return max((k for k in kinds if k in usage), key=lambda k: usage[k])


def _capability(body: str) -> str | None:
    ref = parse_reference(body)
    return ref.capability if ref else None


def estimate_from_history(
    target: int,
    kind: str,
    capability: str | None,
    issues: list[github.IssueSummary],
    usage: dict[int, dict[str, int]],
) -> Estimate:
    """Estimate issue *target* from the closed issues in *issues* with *usage*.

    The past issues of *kind* in *capability* are the basis; with none, every past
    issue of *kind*, at low confidence.
    """
    past = [
        item
        for item in issues
        if not item["is_pull_request"]
        and item["state"] == "closed"
        and item["number"] != target
        and item["number"] in usage
        and issue_kind(usage[item["number"]]) == kind
    ]
    if not past:
        return {"tokens": None, "confidence": "low", "basis": "none", "samples": 0}
    matching = [item for item in past if _capability(item["body"]) == capability]
    chosen = matching or past
    return {
        "tokens": round(mean(sum(usage[item["number"]].values()) for item in chosen)),
        "confidence": "normal" if len(matching) >= NORMAL_CONFIDENCE_SAMPLES else "low",
        "basis": "capability" if matching else "kind",
        "samples": len(chosen),
    }


async def estimate_issue(repo: str, issue: int, kind: db.UsageKind) -> dict[str, Any]:
    """Estimate *issue* in *repo* in tokens from history and store the estimate with it."""
    if issue < 1:
        raise ValueError(f"an estimate must name an issue, got {issue}")
    if kind not in get_args(db.UsageKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(db.UsageKind))}, got {kind!r}")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    issues = await github.list_all_issues(repo)
    target = next((item for item in issues if item["number"] == issue), None)
    if target is None or target["is_pull_request"]:
        raise ValueError(f"Issue {issue} not found in {repo}")
    capability = _capability(target["body"])
    result = estimate_from_history(
        issue, kind, capability, issues, db.usage_by_issue(user_id, repo)
    )
    return db.set_estimate(
        user_id,
        repo,
        issue,
        kind,
        capability,
        result["tokens"],
        result["confidence"],
        result["basis"],
        result["samples"],
    )
