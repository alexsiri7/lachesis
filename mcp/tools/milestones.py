"""Lachesis MCP tool implementations — milestones, whose state is read from the work."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple

import httpx

import db
import github
from derive import landed
from spec_reference import SpecReference

from .repos import _require_user_id
from .requirements import _resolve_repo
from .specs import (
    SpecProgress,
    _not_using_specs,
    read_spec_progress,
    spec_change_requirements,
    spec_change_status,
    uses_specs,
)

logger = logging.getLogger(__name__)

Entries = dict[int, dict[str, Any]]


class Derivation(NamedTuple):
    """A started milestone's state as read from its spec changes and production.

    An inconclusive derivation is one where a spec change could not be read, so
    its state says only that the milestone is not finished.
    """

    state: db.MilestoneState
    conclusive: bool
    last_merge: str | None
    deployment: github.DeploymentVerdict | None


def _is_started(milestone: dict[str, Any]) -> bool:
    return milestone["started_at"] is not None and milestone["dropped_at"] is None


def _closed_out(entry: dict[str, Any]) -> bool:
    """Whether the spec change merged and every issue naming it is closed."""
    derived = entry["derived"]
    return (
        entry["state"] == "merged"
        and derived["source"] == "issues"
        and (derived["status"] == "done" or derived["signals"]["closed_without_merge"])
    )


def _last_merge(entries: list[dict[str, Any]]) -> str | None:
    """The commit of the latest landing among *entries*, all of which landed."""
    latest = max(entries, key=lambda entry: entry["derived"]["landed_at"] or "")
    return latest["derived"]["landed_commit"]


def _partition_readable(
    spec_changes: list[int], entries: Entries
) -> tuple[list[dict[str, Any]], list[int]]:
    """Split *spec_changes* into the entries that were read and the numbers that were not."""
    read: list[dict[str, Any]] = []
    unreadable: list[int] = []
    for number in spec_changes:
        entry = entries.get(number)
        if entry is None or spec_change_status(entry) == "unreadable":
            unreadable.append(number)
        else:
            read.append(entry)
    return read, unreadable


async def derive_milestone_state(
    repo: str, spec_changes: list[int], entries: Entries
) -> Derivation:
    """Derive a started milestone's state from its *spec_changes*' *entries*.

    It is done only once every spec change merged, every issue naming one closed
    by merged code, and production includes the last of those merges; a spec
    change no issue names is unfinished work, not vacuously done.
    """
    read, unreadable = _partition_readable(spec_changes, entries)
    if unreadable:
        return Derivation("active", False, None, None)
    if not read or not all(_closed_out(entry) for entry in read):
        return Derivation("active", True, None, None)
    if not all(entry["derived"]["status"] == "done" for entry in read):
        return Derivation("verifying", True, None, None)
    commit = _last_merge(read)
    if commit is None:
        return Derivation("verifying", True, None, None)
    deployment = await github.get_production_deployment(repo, commit)
    state: db.MilestoneState = (
        "done" if deployment is not None and deployment["state"] == "success" else "verifying"
    )
    return Derivation(state, True, commit, deployment)


def _work(spec_changes: list[int], entries: Entries) -> list[dict[str, Any]]:
    work = []
    for number in spec_changes:
        entry = entries.get(number)
        work.append(
            {
                "spec_change": number,
                "title": entry["title"] if entry else None,
                "url": entry["url"] if entry else None,
                "status": spec_change_status(entry) if entry else "missing",
            }
        )
    return work


async def _view(
    user_id: str, repo: str, milestone: dict[str, Any], entries: Entries
) -> dict[str, Any]:
    """Return *milestone* as read, deriving its state when it has started.

    A conclusive derivation that changed the state records it as an event; a
    failure to record never fails the read, and the next read tries again.
    """
    view: dict[str, Any] = {
        "id": milestone["id"],
        "repo": repo,
        "title": milestone["title"],
        "goal": milestone["goal"],
        "state": "dropped" if milestone["dropped_at"] is not None else "planned",
        "created_at": milestone["created_at"],
        "started_at": milestone["started_at"],
        "dropped_at": milestone["dropped_at"],
        "spec_changes": list(milestone["spec_changes"]),
    }
    if not _is_started(milestone):
        return view
    derivation = await derive_milestone_state(repo, view["spec_changes"], entries)
    view.update(
        state=derivation.state,
        work=_work(view["spec_changes"], entries),
        last_merge=derivation.last_merge,
        deployment=derivation.deployment,
    )
    if derivation.conclusive:
        try:
            db.record_milestone_state(user_id, repo, milestone["id"], derivation.state)
        except Exception:
            logger.warning(
                "could not record the state of milestone %s in %s",
                milestone["id"],
                repo,
                exc_info=True,
            )
    return view


def _entries_of(progress: SpecProgress) -> Entries:
    return {c.entry["spec_change"]: c.entry for c in progress.landing_candidates}


async def _entries(repo: str) -> Entries:
    return _entries_of(await read_spec_progress(repo))


def _require_milestone(user_id: str, repo: str, milestone: int) -> dict[str, Any]:
    row = db.get_milestone(user_id, repo, milestone)
    if row is None:
        raise ValueError(f"Milestone {milestone} not found in {repo}")
    return row


def _check_spec_change(repo: str, spec_change: int, entries: Entries) -> None:
    entry = entries.get(spec_change)
    if entry is None:
        raise ValueError(f"#{spec_change} is not a spec change in {repo}")
    if entry["dropped"]:
        raise ValueError(f"Spec change #{spec_change} was dropped: {entry['reason']}")


async def _refuse_if_done(
    user_id: str, repo: str, milestone: dict[str, Any], entries: Entries, action: str
) -> None:
    if not _is_started(milestone):
        return
    if (await _view(user_id, repo, milestone, entries))["state"] == "done":
        raise ValueError(f"Milestone {milestone['id']} is done; {action}")


async def create_milestone(
    repo: str, title: str, goal: str, spec_changes: list[int] | None = None
) -> dict[str, Any]:
    """Create a planned milestone in *repo* grouping *spec_changes*.

    Each spec change must be one of the repository's spec changes, not dropped,
    and in no other milestone. Returns the milestone as read.
    """
    title = title.strip()
    goal = goal.strip()
    if not title:
        raise ValueError("title must not be empty")
    if not goal:
        raise ValueError("goal must not be empty")
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    if not await uses_specs(repo):
        raise ValueError(_not_using_specs(repo)["message"] + " A milestone groups spec changes.")
    numbers = sorted(set(spec_changes or []))
    if numbers:
        entries = await _entries(repo)
        for number in numbers:
            _check_spec_change(repo, number, entries)
    milestone = db.create_milestone(user_id, repo, title, goal, numbers)
    return await _view(user_id, repo, milestone, {})


async def start_milestone(repo: str, milestone: int) -> dict[str, Any]:
    """Start the planned *milestone* in *repo*. Returns the milestone as read."""
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    row = db.start_milestone(user_id, repo, milestone)
    return await _view(user_id, repo, row, await _entries(repo))


async def drop_milestone(repo: str, milestone: int) -> dict[str, Any]:
    """Drop *milestone* in *repo* unless it is done. Returns the milestone as read."""
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    row = _require_milestone(user_id, repo, milestone)
    entries = await _entries(repo) if _is_started(row) else {}
    await _refuse_if_done(user_id, repo, row, entries, "it cannot be dropped")
    return await _view(user_id, repo, db.drop_milestone(user_id, repo, milestone), {})


async def add_milestone_spec_change(repo: str, milestone: int, spec_change: int) -> dict[str, Any]:
    """Add *spec_change* to *milestone* in *repo* unless it is done or dropped.

    Returns the milestone as read.
    """
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    row = _require_milestone(user_id, repo, milestone)
    if row["dropped_at"] is not None:
        raise ValueError(f"Milestone {milestone} is dropped; spec changes cannot join it")
    entries = await _entries(repo)
    _check_spec_change(repo, spec_change, entries)
    await _refuse_if_done(user_id, repo, row, entries, "its spec changes are fixed")
    row = db.add_milestone_spec_change(user_id, repo, milestone, spec_change)
    return await _view(user_id, repo, row, entries)


async def remove_milestone_spec_change(
    repo: str, milestone: int, spec_change: int
) -> dict[str, Any]:
    """Remove *spec_change* from *milestone* in *repo* unless it is done.

    Returns the milestone as read.
    """
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    row = _require_milestone(user_id, repo, milestone)
    entries = await _entries(repo) if _is_started(row) else {}
    await _refuse_if_done(user_id, repo, row, entries, "its spec changes are fixed")
    row = db.remove_milestone_spec_change(user_id, repo, milestone, spec_change)
    return await _view(user_id, repo, row, entries)


async def get_milestone(repo: str, milestone: int) -> dict[str, Any]:
    """Return *milestone* in *repo* as read, its state derived from the work."""
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    row = _require_milestone(user_id, repo, milestone)
    entries = await _entries(repo) if _is_started(row) else {}
    return await _view(user_id, repo, row, entries)


async def list_milestones(repo: str) -> list[dict[str, Any]]:
    """Return every milestone in *repo* as read, oldest first."""
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    rows = db.list_milestones(user_id, repo)
    entries = await _entries(repo) if any(_is_started(row) for row in rows) else {}
    return [await _view(user_id, repo, row, entries) for row in rows]


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _names(ref: SpecReference, spec_change: int, capability: str, requirement: str) -> bool:
    """Whether *ref* names *requirement* of *capability* in *spec_change*."""
    return (
        ref.spec_change == spec_change
        and ref.capability == capability
        and ref.requirement is not None
        and _normalized(ref.requirement) == _normalized(requirement)
    )


async def _requirements_without_issues(
    repo: str, spec_changes: list[int], references: dict[int, SpecReference]
) -> tuple[list[dict[str, Any]], set[int]]:
    """Return the requirements *spec_changes* add or change that no issue names.

    Also returns the spec changes whose requirements could not be read.
    """
    results = await asyncio.gather(
        *(spec_change_requirements(repo, number) for number in spec_changes),
        return_exceptions=True,
    )
    gaps: list[dict[str, Any]] = []
    unreadable: set[int] = set()
    for number, result in zip(spec_changes, results, strict=True):
        if isinstance(result, BaseException) and not isinstance(result, httpx.HTTPError):
            raise result
        if result is None or isinstance(result, BaseException):
            logger.warning(
                "could not read the requirements of spec change %s in %s: %r",
                number,
                repo,
                result,
            )
            unreadable.add(number)
            continue
        gaps.extend(
            {
                "spec_change": number,
                "capability": capability,
                "requirement": requirement,
                "change": change,
            }
            for capability, requirement, change in result
            if not any(_names(ref, number, capability, requirement) for ref in references.values())
        )
    return gaps, unreadable


async def _not_in_production(repo: str, entry: dict[str, Any]) -> dict[str, Any] | None:
    derived = entry["derived"]
    commit = derived["landed_commit"]
    if derived["status"] != "done" or commit is None:
        return None
    deployment = await github.get_production_deployment(repo, commit)
    if deployment is not None and deployment["state"] == "success":
        return None
    return {"spec_change": entry["spec_change"], "commit": commit, "deployment": deployment}


def _blocking_questions(
    user_id: str, repo: str, milestone: int, spec_changes: list[int], issues: set[int]
) -> list[dict[str, Any]]:
    return [
        question
        for question in db.list_open_questions(user_id)
        if question["repo_name"] == repo
        and (
            issues.intersection(question["blocks"])
            or question["milestone"] == str(milestone)
            or question["spec_change"] in spec_changes
        )
    ]


async def review_milestone(repo: str, milestone: int) -> dict[str, Any]:
    """Return started *milestone* in *repo* as read, with what it is missing.

    ``gaps`` holds the requirements its spec changes add or change that no issue
    names, the issues closed without a merged pull request, the spec changes merged
    but not yet in production, and the open questions blocking its work.  Gaps are
    only claimed about what was read: ``unreadable`` lists the spec changes that
    could not be, which are never clean.
    """
    user_id = _require_user_id()
    repo = await _resolve_repo(repo, require_registered=True)
    row = _require_milestone(user_id, repo, milestone)
    if not _is_started(row):
        state = "dropped" if row["dropped_at"] is not None else "planned"
        raise ValueError(f"Milestone {milestone} is {state}; only a started milestone is reviewed")
    progress = await read_spec_progress(repo)
    entries = _entries_of(progress)
    view = await _view(user_id, repo, row, entries)
    spec_changes: list[int] = view["spec_changes"]

    read, unread_entries = _partition_readable(spec_changes, entries)
    issues = {n for n, ref in progress.references.items() if ref.spec_change in spec_changes}
    closed_without_merge = [
        {"issue": n, "spec_change": entry["spec_change"]}
        for entry in read
        for n in sorted(entry["derived"]["linked_issues"])
        if (facts := progress.facts.get(n)) is not None
        and facts["state"] == "CLOSED"
        and not landed(facts)
    ]
    lagging = await asyncio.gather(*(_not_in_production(repo, entry) for entry in read))
    requirements, unread_requirements = await _requirements_without_issues(
        repo,
        [entry["spec_change"] for entry in read if entry["state"] != "closed"],
        progress.references,
    )
    unreadable = set(unread_entries) | unread_requirements
    return {
        **view,
        "gaps": {
            "requirements_without_issues": requirements,
            "issues_closed_without_merge": closed_without_merge,
            "merges_not_in_production": [gap for gap in lagging if gap is not None],
            "open_questions": _blocking_questions(user_id, repo, milestone, spec_changes, issues),
        },
        "unreadable": [number for number in spec_changes if number in unreadable],
    }
