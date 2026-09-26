"""Requirement status read off the GitHub issues a requirement links to.

The ``status`` field in a requirement file is hand-maintained, so it drifts from
what actually happened to the work.  These helpers compute a second status from
the issues and their pull requests, so a listing can report both and say when
they disagree.  A spec change records no status, so for one the derived status
is the only one there is.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

import frontmatter

from status import INACTIVE_STATUSES, canonical_status

DerivedStatus = Literal["idea", "in-progress", "done"]
DerivationSource = Literal["issues", "unlinked", "unavailable"]
UnlinkedKind = Literal["awaiting_pr", "never_linked"]
ActivityFailure = Literal["rate_limited", "inaccessible", "transient"]
UnavailableCause = Literal["rate_limited", "inaccessible", "transient", "issue_missing"]
SpecChangeState = Literal["open", "merged", "closed"]

PROGRESS_RANK: dict[str, int] = {
    "draft": 0,
    "idea": 1,
    "planned": 1,
    "in-progress": 2,
    "done": 3,
}

FAILING_CONCLUSIONS: frozenset[str] = frozenset(
    {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ERROR"}
)

_IN_PROGRESS_LABEL = "archon:in-progress"

_ISSUES_SECTION = re.compile(r"^## Issues\b[^\n]*\n(?:(?!^## )[^\n]*\n?)*", re.MULTILINE)
_ISSUE_BULLET = re.compile(r"^\s*[-*]\s*#(\d+)\b")


class CheckFacts(TypedDict):
    """One check context on a pull request's head commit."""

    conclusion: str | None
    at: str | None


class PullFacts(TypedDict):
    """A pull request that declares it closes one of the requirement's issues."""

    number: int
    state: str
    merged: bool
    updated_at: str | None
    merged_at: str | None
    merge_commit: str | None
    is_draft: bool
    checks: list[CheckFacts]


class BlockerFacts(TypedDict):
    """An open issue a linked issue declares itself blocked by, possibly elsewhere."""

    repo: str
    number: int
    last_activity: str | None


class IssueFacts(TypedDict):
    """The observable state of one linked issue."""

    number: int
    state: str
    updated_at: str | None
    closed_at: str | None
    labels: list[str]
    closed_by_commit: bool
    blocked_by: list[BlockerFacts]
    pulls: list[PullFacts]


class Signals(TypedDict):
    """States that look like progress but are not, each reported on its own."""

    closed_without_merge: bool
    closed_but_recorded_in_progress: bool
    checks_failing_since: str | None
    unlinked: UnlinkedKind | None


class Derived(TypedDict):
    """The derived view reported alongside a requirement's recorded status."""

    status: DerivedStatus | None
    source: DerivationSource
    unavailable_cause: UnavailableCause | None
    diverged: bool
    last_activity: str | None
    landed_at: str | None
    landed_commit: str | None
    linked_issues: list[int]
    blocked_by: list[BlockerFacts]
    signals: Signals


def linked_issue_numbers(post: frontmatter.Post) -> list[int]:
    """Return the issue numbers a requirement links to, in document order.

    The ``## Issues`` bullets are the source: ``link_issues`` writes every issue
    there while frontmatter keeps only the first, so ``github_issue`` is unioned
    in rather than trusted alone.  Only a number opening a bullet counts, which
    leaves cross-repo references and prose mentions out.
    """
    numbers: list[int] = []
    section = _ISSUES_SECTION.search(post.content)
    if section:
        for line in section.group(0).splitlines():
            match = _ISSUE_BULLET.match(line)
            if match and int(match.group(1)) not in numbers:
                numbers.append(int(match.group(1)))
    recorded = post.get("github_issue")
    if isinstance(recorded, int) and recorded not in numbers:
        numbers.append(recorded)
    return numbers


def _landed(issue: IssueFacts) -> bool:
    """True when the issue was closed by code that merged.

    A squash merge closes its issue through the squash commit, which leaves
    ``closedByPullRequestsReferences`` empty — the closing commit is then the
    only evidence the work landed.
    """
    return issue["closed_by_commit"] or any(pull["merged"] for pull in issue["pulls"])


def _last_activity(issues: list[IssueFacts]) -> str | None:
    stamps = [
        stamp
        for issue in issues
        for stamp in (issue["updated_at"], *(pull["updated_at"] for pull in issue["pulls"]))
        if stamp
    ]
    return max(stamps) if stamps else None


def _issue_landed_at(issue: IssueFacts) -> str | None:
    """When this issue's work landed, preferring the merge over the close.

    A squash merge closes its issue through the squash commit, so an issue can
    carry both a merged pull request and a commit closer.  The merge is the
    moment the work landed; the close time only stands in when no merged pull
    request offers a timestamp.
    """
    merges = [pull["merged_at"] for pull in issue["pulls"] if pull["merged"] and pull["merged_at"]]
    if merges:
        return max(merges)
    return issue["closed_at"] if issue["closed_by_commit"] else None


def _landed_at(issues: list[IssueFacts]) -> str | None:
    """When the last of the requirement's issues landed."""
    stamps = [stamp for issue in issues if (stamp := _issue_landed_at(issue))]
    return max(stamps) if stamps else None


def _landed_commit(issues: list[IssueFacts]) -> str | None:
    """The commit of the last pull request to merge, across every linked issue.

    Taken across the issues directly rather than off whichever landing supplied
    ``landed_at``, which can be a squash-close naming no pull request.  It is
    ``None`` only when no linked pull request merged at all, and deployment
    questions then have nothing to ask about.
    """
    merges = [
        (pull["merged_at"], pull["merge_commit"])
        for issue in issues
        for pull in issue["pulls"]
        if pull["merged"] and pull["merged_at"] is not None
    ]
    return max(merges, key=lambda merge: merge[0])[1] if merges else None


def _blocking_issues(issues: list[IssueFacts]) -> list[BlockerFacts]:
    """Return the open blockers of the requirement's still-open issues.

    A closed issue explains nothing about why the requirement is quiet, so its
    blockers are left out.  The same blocker reached through two linked issues
    is reported once.
    """
    unique: dict[tuple[str, int], BlockerFacts] = {}
    for issue in issues:
        if issue["state"] == "CLOSED":
            continue
        for blocker in issue["blocked_by"]:
            unique.setdefault((blocker["repo"], blocker["number"]), blocker)
    return [unique[key] for key in sorted(unique)]


def _checks_failing_since(issues: list[IssueFacts]) -> str | None:
    """Return the earliest failing check across the open, non-draft pull requests.

    Keyed off the check rather than the pull request: a pull request's own
    timestamp moves on every comment or push, which would report a fresh time
    for checks that have been broken for a week.
    """
    stamps = [
        check["at"]
        for issue in issues
        for pull in issue["pulls"]
        if pull["state"] == "OPEN" and not pull["is_draft"]
        for check in pull["checks"]
        if check["at"] and check["conclusion"] in FAILING_CONCLUSIONS
    ]
    return min(stamps) if stamps else None


def _empty_signals() -> Signals:
    return {
        "closed_without_merge": False,
        "closed_but_recorded_in_progress": False,
        "checks_failing_since": None,
        "unlinked": None,
    }


def derive(
    recorded_status: str | None,
    numbers: list[int],
    facts: dict[int, IssueFacts],
    *,
    on_pr_branch: bool,
    failure: ActivityFailure | None = None,
) -> Derived:
    """Derive a requirement's status from *facts* about the issues in *numbers*.

    *facts* holds every issue that was read and is routinely partial: *failure*
    names what went wrong reading the rest, and a requirement whose own issues
    came back derives normally regardless of it.  Issues absent from *facts*
    without a *failure* have been deleted or transferred, so a requirement whose
    issues are all missing reports as unreadable rather than as untouched work,
    and ``unavailable_cause`` says which of the two happened.

    Only ``idea``, ``in-progress`` and ``done`` are ever derived.  ``draft`` and
    ``planned`` are author bookkeeping with no work-side signature, and
    ``cancelled`` / ``superseded`` are author intent that an observation must
    never contradict — those are reported as diverging from nothing.  A
    *recorded_status* of ``None`` is work that records no status, which diverges
    from nothing either.
    """
    signals = _empty_signals()
    if not numbers:
        signals["unlinked"] = "awaiting_pr" if on_pr_branch else "never_linked"
        return {
            "status": None,
            "source": "unlinked",
            "unavailable_cause": None,
            "diverged": False,
            "last_activity": None,
            "landed_at": None,
            "landed_commit": None,
            "linked_issues": [],
            "blocked_by": [],
            "signals": signals,
        }

    issues = [facts[number] for number in numbers if number in facts]
    if not issues:
        return {
            "status": None,
            "source": "unavailable",
            "unavailable_cause": failure or "issue_missing",
            "diverged": False,
            "last_activity": None,
            "landed_at": None,
            "landed_commit": None,
            "linked_issues": numbers,
            "blocked_by": [],
            "signals": signals,
        }

    recorded = None if recorded_status is None else canonical_status(recorded_status)
    closed = [issue for issue in issues if issue["state"] == "CLOSED"]
    status: DerivedStatus
    if len(closed) == len(issues):
        if all(_landed(issue) for issue in issues):
            status = "done"
        else:
            status = "in-progress"
            signals["closed_without_merge"] = True
        signals["closed_but_recorded_in_progress"] = recorded == "in-progress"
    elif (
        closed
        or any(issue["pulls"] for issue in issues)
        or any(_IN_PROGRESS_LABEL in issue["labels"] for issue in issues)
    ):
        status = "in-progress"
    else:
        status = "idea"
    signals["checks_failing_since"] = _checks_failing_since(issues)
    landed = status == "done"

    return {
        "status": status,
        "source": "issues",
        "unavailable_cause": None,
        "diverged": (
            recorded not in INACTIVE_STATUSES
            and recorded in PROGRESS_RANK
            and PROGRESS_RANK[recorded] != PROGRESS_RANK[status]
        ),
        "last_activity": _last_activity(issues),
        "landed_at": _landed_at(issues) if landed else None,
        "landed_commit": _landed_commit(issues) if landed else None,
        "linked_issues": numbers,
        "blocked_by": _blocking_issues(issues),
        "signals": signals,
    }


def derive_spec_change(
    state: SpecChangeState,
    updated_at: str | None,
    numbers: list[int],
    facts: dict[int, IssueFacts],
    *,
    failure: ActivityFailure | None = None,
) -> Derived:
    """Derive a spec change's status from the issues in *numbers*, those naming it.

    A spec change records no status of its own, so it never diverges.  Its pull
    request is part of the work: while open it is what the change awaits, and its
    own movement counts towards ``last_activity``.
    """
    derived = derive(None, numbers, facts, on_pr_branch=state == "open", failure=failure)
    stamps = [stamp for stamp in (derived["last_activity"], updated_at) if stamp]
    derived["last_activity"] = max(stamps) if stamps else None
    return derived


def spec_change_dropped(
    state: SpecChangeState, numbers: list[int], facts: dict[int, IssueFacts]
) -> str | None:
    """Say why a spec change was dropped, or ``None`` when it was not.

    Every issue has to have been read before all of them can be called abandoned.
    """
    if state == "closed":
        return "its pull request closed without merging"
    issues = [facts[number] for number in numbers if number in facts]
    if (
        issues
        and len(issues) == len(numbers)
        and all(issue["state"] == "CLOSED" and not _landed(issue) for issue in issues)
    ):
        return "every issue naming it closed without a merged pull request"
    return None
