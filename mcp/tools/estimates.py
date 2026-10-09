"""Lachesis MCP tool implementations — estimates: what a point costs, from history, and so
what an issue should cost."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple, TypedDict, get_args

import db
import github
from spec_reference import parse_reference

from .repos import _require_user_id, normalize_repo
from .requirements import _resolve_repo

logger = logging.getLogger(__name__)

NORMAL_CONFIDENCE_SAMPLES = 5


class PastIssue(NamedTuple):
    repo: str
    number: int
    kind: str
    model: str
    tokens: int
    size: int


class Price(TypedDict):
    tokens_per_point: float | None
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


def past_issues(
    repo: str,
    issues: list[github.IssueSummary],
    usage: dict[int, dict[str, int]],
    models: dict[int, dict[str, int]],
    sizes: dict[int, int],
) -> list[PastIssue]:
    """Return the closed issues in *issues* with *usage* and a size of at most 13.

    A past issue's kind of work and model are the ones that used the most tokens on it;
    its tokens are every token reported on it.
    """
    return [
        PastIssue(
            repo,
            item["number"],
            issue_kind(usage[item["number"]]),
            max(models[item["number"]], key=models[item["number"]].__getitem__),
            sum(usage[item["number"]].values()),
            sizes[item["number"]],
        )
        for item in issues
        if not item["is_pull_request"]
        and item["state"] == "closed"
        and item["number"] in usage
        and sizes.get(item["number"], db.TOO_BIG) != db.TOO_BIG
    ]


async def factory_history(user_id: str) -> tuple[list[PastIssue], list[str]]:
    """Return the past issues of every registered repository, and the repositories whose
    issues could not be listed."""
    sizes = db.sizes_by_issue(user_id)
    past: list[PastIssue] = []
    unreadable: list[str] = []
    for repo_name in [row["repo_name"] for row in db.list_repos(user_id)]:
        repo = normalize_repo(repo_name)
        try:
            issues = await github.list_all_issues(repo)
        except Exception:
            logger.warning("could not list the issues of %s to price a point", repo)
            unreadable.append(repo)
            continue
        past += past_issues(
            repo,
            issues,
            db.usage_by_issue(user_id, repo_name),
            db.usage_by_issue(user_id, repo_name, by="model"),
            {issue: size for (name, issue), size in sizes.items() if name == repo_name},
        )
    return past, unreadable


def _price(
    past: list[PastIssue], confidence: db.EstimateConfidence, basis: db.EstimateBasis
) -> Price:
    return {
        "tokens_per_point": sum(p.tokens for p in past) / sum(p.size for p in past),
        "confidence": confidence,
        "basis": basis,
        "samples": len(past),
    }


def price_per_point(
    past: list[PastIssue], kind: str | None = None, model: str | None = None
) -> Price:
    """Return the tokens a point of *kind* work on *model* costs in *past*.

    Leaving out *kind* or *model* prices every kind or model. From fewer than
    ``NORMAL_CONFIDENCE_SAMPLES`` matching issues, the price is the one across all kinds
    and models, at low confidence. With no past issues there is no price.
    """
    matching = [p for p in past if kind in (None, p.kind) and model in (None, p.model)]
    if len(matching) >= NORMAL_CONFIDENCE_SAMPLES:
        return _price(matching, "normal", "kind")
    if past:
        return _price(past, "low", "all kinds")
    return {"tokens_per_point": None, "confidence": "low", "basis": "none", "samples": 0}


def estimate_tokens(size: int | None, price: Price) -> int | None:
    """Return an issue of *size* priced at *price*; none for an unsized issue, one too big
    to plan, or with no price."""
    if size is None or size == db.TOO_BIG or price["tokens_per_point"] is None:
        return None
    return round(size * price["tokens_per_point"])


async def estimate_issue(
    repo: str, issue: int, kind: db.UsageKind, model: str | None = None
) -> dict[str, Any]:
    """Estimate *issue* in *repo* in tokens, its size times the price of a point of *kind*
    work on *model*, and store the estimate with it."""
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
    history, _ = await factory_history(user_id)
    others = [p for p in history if (p.repo, p.number) != (normalize_repo(repo), issue)]
    price = price_per_point(others, kind, model)
    size = db.sizes_by_issue(user_id, repo).get((repo, issue))
    tokens = estimate_tokens(size, price)
    stored = db.set_estimate(
        user_id,
        repo,
        issue,
        kind,
        _capability(target["body"]),
        tokens,
        price["confidence"],
        price["basis"],
        price["samples"],
    )
    del stored["tokens"]
    return {
        **stored,
        "model": model,
        "size": size,
        "estimate_tokens": tokens,
        "tokens_per_point": price["tokens_per_point"],
    }


async def size_issue(repo: str, issue: int, size: int) -> dict[str, Any]:
    """Set *issue*'s size in Fibonacci points: 1, 2, 3, 5, 8 or 13, or 21 for an issue
    larger than 13, which is not planned until it is split."""
    if issue < 1:
        raise ValueError(f"a size must name an issue, got {issue}")
    db.check_size(size)
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    return db.set_size(user_id, repo, issue, size)
