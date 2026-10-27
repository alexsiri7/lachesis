"""Configure a repository so its pull requests merge without a human.

Auto-merge allowed, merged branches deleted, and no approving review required on the
default branch, by classic branch protection or by a ruleset.  Required status checks
are never touched: they are what an unattended merge waits for.  Only what differs is
changed, so a repository already prepared needs no permission to change anything.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

import github

logger = logging.getLogger(__name__)

REPO_SETTINGS = {"allow_auto_merge": True, "delete_branch_on_merge": True}
BRANCH_PROTECTION_REVIEWS = "branch protection required_approving_review_count"
RULESETS = "ruleset required_approving_review_count"

# Classic branch protection and rulesets name the same review requirements differently.
_PROTECTION_APPROVALS = {
    "required_approving_review_count": 0,
    "require_code_owner_reviews": False,
    "require_last_push_approval": False,
}
_RULESET_APPROVALS = {
    "required_approving_review_count": 0,
    "require_code_owner_review": False,
    "require_last_push_approval": False,
}


async def configure(repo: str) -> dict[str, str]:
    """Configure *repo*, returning each setting that could not be, mapped to why."""
    try:
        current = await github.get_repo_settings(repo)
    except Exception as exc:
        reason = f"its settings could not be read: {_reason(exc)}"
        return dict.fromkeys((*REPO_SETTINGS, BRANCH_PROTECTION_REVIEWS, RULESETS), reason)
    refused: dict[str, str] = {}
    changes = {key: value for key, value in REPO_SETTINGS.items() if current[key] != value}
    if changes:
        try:
            await github.update_repo_settings(repo, changes)
        except Exception as exc:
            refused.update(dict.fromkeys(changes, _reason(exc)))
    branch = current["default_branch"]
    try:
        await _drop_protection_approvals(repo, branch)
    except Exception as exc:
        refused[BRANCH_PROTECTION_REVIEWS] = _reason(exc)
    try:
        refused.update(await _drop_ruleset_approvals(repo, branch))
    except Exception as exc:
        refused[RULESETS] = f"the rulesets could not be read: {_reason(exc)}"
    if refused:
        logger.warning("unattended merges: repo=%s refused=%s", repo, refused)
    return refused


async def _drop_protection_approvals(repo: str, branch: str) -> None:
    protection = await github.get_branch_protection(repo, branch)
    reviews = (protection or {}).get("required_pull_request_reviews")
    if reviews and _requires_approval(reviews, _PROTECTION_APPROVALS):
        await github.update_required_reviews(repo, branch, _PROTECTION_APPROVALS)


async def _drop_ruleset_approvals(repo: str, branch: str) -> dict[str, str]:
    refused: dict[str, str] = {}
    approving = {
        rule["ruleset_id"]: rule
        for rule in await github.list_branch_rules(repo, branch)
        if rule.get("type") == "pull_request"
        and _requires_approval(rule.get("parameters") or {}, _RULESET_APPROVALS)
    }
    for ruleset_id, rule in approving.items():
        setting = f"ruleset {ruleset_id} required_approving_review_count"
        if rule.get("ruleset_source_type") != "Repository":
            refused[setting] = (
                f"the ruleset belongs to {rule.get('ruleset_source')}, not to {repo}, "
                "so only its owner can change it"
            )
            continue
        try:
            ruleset = await github.get_ruleset(repo, ruleset_id)
            await github.update_ruleset_rules(
                repo, ruleset_id, [_without_approvals(r) for r in ruleset["rules"]]
            )
        except Exception as exc:
            refused[setting] = _reason(exc)
    return refused


def _requires_approval(reviews: dict[str, Any], none_required: dict[str, Any]) -> bool:
    return any(reviews.get(key, value) != value for key, value in none_required.items())


def _without_approvals(rule: dict[str, Any]) -> dict[str, Any]:
    if rule.get("type") != "pull_request":
        return rule
    return {**rule, "parameters": {**(rule.get("parameters") or {}), **_RULESET_APPROVALS}}


def _reason(exc: Exception) -> str:
    """Name why GitHub refused, in its own words where it gave them."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            message = exc.response.json().get("message")
        except ValueError:
            message = None
        return f"GitHub answered {exc.response.status_code}: {message or exc.response.text}"
    return repr(exc)
