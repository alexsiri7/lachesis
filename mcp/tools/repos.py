"""Lachesis MCP tool implementations — repo registry management."""

from __future__ import annotations

import logging
import re
from typing import Any

import db
import unattended_merges
from auth import current_db_user_id

logger = logging.getLogger(__name__)

_GITHUB_URL_RE = re.compile(r"(?:https?://)?github\.com/([^/]+/[^/]+?)(?:\.git)?/?$")


def normalize_repo(repo: str) -> str:
    """Normalize a GitHub repo identifier to owner/repo format.

    Accepts full URLs (https://github.com/owner/repo) or bare owner/repo strings.
    """
    repo = repo.strip()
    m = _GITHUB_URL_RE.match(repo)
    if m:
        return m.group(1)
    if "/" not in repo:
        raise ValueError("repo must be in owner/repo format, e.g. alexsiri7/lachesis")
    parts = repo.split("/")
    if len(parts) == 2 and parts[0] and parts[1]:
        return repo
    raise ValueError(f"invalid repo format: {repo!r}")


def _require_user_id() -> str:
    """Return the authenticated DB user ID or raise."""
    user_id = current_db_user_id.get()
    if not user_id:
        raise RuntimeError(
            "No DB user ID in request context. "
            "This may indicate a database connectivity issue (check server logs) "
            "or a token that predates the lachesis_uid claim."
        )
    return user_id


LANDING_SIGNALS = ("merge", "deployment")


async def register_repo(repo: str, landing_signal: str | None = None) -> dict[str, Any]:
    """Register a repo so Lachesis tracks it. Omitting *landing_signal* leaves it alone.

    The repo is then configured for unattended merges; the registration stands whatever
    GitHub refuses, and ``merge_setup_refusals`` maps each setting it refused to why.
    """
    user_id = _require_user_id()
    if landing_signal is not None and landing_signal not in LANDING_SIGNALS:
        raise ValueError(
            f"landing_signal must be one of {', '.join(LANDING_SIGNALS)}, got {landing_signal!r}"
        )
    normalized = normalize_repo(repo)
    row = db.register_repo(user_id, normalized, landing_signal)
    return {**row, "merge_setup_refusals": await prepare_for_unattended_merges(user_id, normalized)}


async def prepare_for_unattended_merges(user_id: str, repo_name: str) -> dict[str, str]:
    """Configure the repo registered as *repo_name* for unattended merges and record what
    GitHub refused against that row, whichever form its name is stored in."""
    refusals = await unattended_merges.configure(normalize_repo(repo_name))
    db.record_merge_setup_refusals(user_id, repo_name, refusals)
    return refusals


async def prepare_registered_repos() -> None:
    """Configure every registered repo for unattended merges, as each start does once."""
    for row in db.list_all_repos():
        try:
            await prepare_for_unattended_merges(str(row["user_id"]), row["repo_name"])
        except Exception:
            logger.exception("could not prepare repo=%s for unattended merges", row["repo_name"])


async def unregister_repo(repo: str) -> dict[str, Any]:
    """Unregister a repo from Lachesis tracking."""
    user_id = _require_user_id()
    normalized = normalize_repo(repo)
    db.unregister_repo(user_id, normalized)
    if repo != normalized:
        # Also purge any stale full-URL rows for the same repo.
        db.unregister_repo(user_id, repo)
    return {"unregistered": normalized}


async def list_repos() -> list[dict[str, Any]]:
    """List all repos registered with Lachesis."""
    user_id = _require_user_id()
    rows = db.list_repos(user_id)
    for row in rows:
        row["repo_name"] = normalize_repo(row["repo_name"])
    return rows
