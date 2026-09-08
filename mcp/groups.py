"""Partition an already-derived requirement corpus into the dashboard's groups.

The dashboard's readers ask what shipped, what was dropped and what went quiet.
Every fact needed is on a requirement's ``derived`` block already; what is
missing is a verdict, so these helpers sort the corpus into seven named groups
and threshold silence into a staleness judgement.

Kept apart from :mod:`derive`, whose contract is one requirement's issue facts:
grouping needs a clock and a threshold, and is a pure function of the corpus.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple

from derive import BlockerFacts
from status import INACTIVE_STATUSES

DEFAULT_STALE_AFTER_DAYS = 14

GROUP_NAMES = (
    "landed",
    "dropped",
    "awaiting_merge",
    "stalled",
    "needs_attention",
    "diverged",
    "in_flight",
)

NEEDS_ATTENTION_KINDS = (
    "closed_without_merge",
    "checks_failing_since",
    "closed_but_recorded_in_progress",
    "unavailable",
    "never_linked",
)


class Candidate(NamedTuple):
    """One requirement the dashboard read, with the repo it was read from."""

    repo: str
    entry: dict[str, Any]
    linked_issues: list[int]


def days_since(stamp: str | None, now: datetime) -> int | None:
    """Whole days from *stamp* to *now*, or ``None`` when that cannot be said.

    A timestamp GitHub returned in a shape we cannot parse must not cost the
    caller the dashboard they asked for, so this reports "not computed" rather
    than raising.  The result is not clamped: a stamp in the future is negative,
    which reads as recent activity everywhere it is used.
    """
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return (now - parsed).days


def _needs_attention_kinds(derived: dict[str, Any]) -> list[str]:
    """Name every fault the derivation reported, in the order they matter."""
    signals = derived.get("signals") or {}
    present = {
        "closed_without_merge": bool(signals.get("closed_without_merge")),
        "checks_failing_since": bool(signals.get("checks_failing_since")),
        "closed_but_recorded_in_progress": bool(signals.get("closed_but_recorded_in_progress")),
        "unavailable": derived.get("source") == "unavailable",
        "never_linked": signals.get("unlinked") == "never_linked",
    }
    return [kind for kind in NEEDS_ATTENTION_KINDS if present[kind]]


def _explained_by_a_moving_blocker(
    blockers: list[BlockerFacts], now: datetime, stale_after_days: int
) -> bool:
    """True when an open blocker is itself still moving, which explains the silence.

    Being blocked is not stalling, but being blocked behind something itself
    quiet is: a blocker nobody has touched either explains nothing.
    """
    for blocker in blockers:
        quiet = days_since(blocker["last_activity"], now)
        if quiet is None or quiet < stale_after_days:
            return True
    return False


def _base_fields(
    candidate: Candidate, derived: dict[str, Any], days_quiet: int | None
) -> dict[str, Any]:
    entry = candidate.entry
    return {
        "repo": candidate.repo,
        "id": entry["id"],
        "title": entry["title"],
        "status": entry["status"],
        "derived_status": derived.get("status"),
        "last_activity": derived.get("last_activity"),
        "days_quiet": days_quiet,
        "github_issue": entry.get("github_issue"),
        "linked_issues": candidate.linked_issues,
    }


def _by_repo_and_id(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda entry: (entry["repo"], entry["id"]))


def _sorted_by(
    entries: list[dict[str, Any]],
    key_fn: Callable[[dict[str, Any]], Any],
    *,
    reverse: bool,
) -> list[dict[str, Any]]:
    """Sort *entries* on *key_fn* with the unknowns last, ties by repo and id.

    An entry whose *key_fn* reads ``None`` is unknown.  Reversing a single sort
    key would put those first and cannot be applied to the string timestamps, so
    the two halves are ordered separately; the presort is what makes repo and id
    break ties in either direction.  Where the key is when a pull request was
    opened, PR numbers cannot stand in for the stamp: they are per-repo, and that
    group spans repos.
    """
    known = [entry for entry in entries if key_fn(entry) is not None]
    unknown = [entry for entry in entries if key_fn(entry) is None]
    known.sort(key=lambda entry: (entry["repo"], entry["id"]))
    known.sort(key=key_fn, reverse=reverse)
    return known + _by_repo_and_id(unknown)


def group_requirements(
    candidates: list[Candidate], *, now: datetime, stale_after_days: int
) -> dict[str, list[dict[str, Any]]]:
    """Sort *candidates* into the seven dashboard groups, each landing in exactly one.

    The groups are tested in the order dropped, awaiting merge, landed, needs
    attention, stalled, diverged, in flight.  Recorded intent to drop the work
    outranks any observation of it — a cancelled requirement's open PR wants
    closing, not merging.  Below that, a requirement file still sitting on an
    open pull request outranks everything: the group is only trustworthy as the
    answer to "which requirement PRs are unmerged?" if nothing can hide a
    member, and ``landed`` is the most misleading hiding place of all, reading
    as "this shipped" for the file most likely forgotten.  So a requirement
    whose work landed is reported as landed even when its file was never updated
    to say so — those lagging records are exactly the ones derivation exists to
    find — unless that file is itself still on a PR.  A named fault outranks the
    symptom of silence, and forgotten work outranks stale bookkeeping.

    Every group is present, empty when nothing matched.
    """
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in GROUP_NAMES}

    for candidate in candidates:
        derived = candidate.entry.get("derived") or {}
        days_quiet = days_since(derived.get("last_activity"), now)
        entry = _base_fields(candidate, derived, days_quiet)
        blockers = derived.get("blocked_by") or []
        if blockers:
            entry["blocked_by"] = blockers
        if derived.get("unavailable_cause"):
            entry["unavailable_cause"] = derived["unavailable_cause"]

        kinds = _needs_attention_kinds(derived)
        if candidate.entry["status"] in INACTIVE_STATUSES:
            for key in ("reason", "superseded_by"):
                if candidate.entry.get(key):
                    entry[key] = candidate.entry[key]
            grouped["dropped"].append(entry)
        elif candidate.entry.get("pr"):
            entry["pr"] = candidate.entry["pr"]
            if kinds:
                entry["kinds"] = kinds
            grouped["awaiting_merge"].append(entry)
        elif derived.get("status") == "done":
            entry["landed_at"] = derived.get("landed_at")
            grouped["landed"].append(entry)
        elif kinds:
            entry["kinds"] = kinds
            grouped["needs_attention"].append(entry)
        elif (
            days_quiet is not None
            and days_quiet >= stale_after_days
            and not _explained_by_a_moving_blocker(blockers, now, stale_after_days)
        ):
            grouped["stalled"].append(entry)
        elif derived.get("diverged"):
            grouped["diverged"].append(entry)
        else:
            grouped["in_flight"].append(entry)

    grouped["awaiting_merge"] = _sorted_by(
        grouped["awaiting_merge"],
        lambda entry: entry["pr"].get("created_at") or None,
        reverse=False,
    )
    grouped["landed"] = _sorted_by(
        grouped["landed"], lambda entry: entry["landed_at"], reverse=True
    )
    grouped["stalled"] = _sorted_by(
        grouped["stalled"], lambda entry: entry["days_quiet"], reverse=True
    )
    grouped["needs_attention"] = _sorted_by(
        grouped["needs_attention"], lambda entry: entry["days_quiet"], reverse=True
    )
    grouped["needs_attention"].sort(
        key=lambda entry: NEEDS_ATTENTION_KINDS.index(entry["kinds"][0])
    )
    for name in ("dropped", "diverged", "in_flight"):
        grouped[name] = _by_repo_and_id(grouped[name])
    return grouped
