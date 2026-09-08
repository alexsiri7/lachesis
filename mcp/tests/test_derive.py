"""Unit tests for mcp/derive.py — status read off a requirement's issues."""

from __future__ import annotations

import frontmatter

from derive import (
    BlockerFacts,
    CheckFacts,
    IssueFacts,
    PullFacts,
    derive,
    linked_issue_numbers,
)

_BULLETS_MD = """\
---
id: "030"
title: "Track story submissions"
status: "draft"
github_issue: null
created: "2026-05-11"
updated: "2026-05-11"
---

## Why

Submissions are tracked in a spreadsheet. See #999 for the history.

## Issues

- #1052 — Model submissions
- #1053 — Import the spreadsheet
- #1054 — Submission list view
- #1081 — Contest deadlines

## Notes

Follow-up lives in #2000.
"""

_EMPTY_ISSUES_MD = """\
---
id: "001"
title: "User authentication"
status: "draft"
github_issue: null
---

## Issues

_None yet._
"""


def _pull(
    number: int = 500,
    *,
    state: str = "OPEN",
    merged: bool = False,
    updated_at: str = "2026-08-01T00:00:00Z",
    merged_at: str | None = None,
    merge_commit: str | None = None,
    is_draft: bool = False,
    checks: list[CheckFacts] | None = None,
) -> PullFacts:
    return {
        "number": number,
        "state": state,
        "merged": merged,
        "updated_at": updated_at,
        "merged_at": merged_at,
        "merge_commit": merge_commit,
        "is_draft": is_draft,
        "checks": checks or [],
    }


def _issue(
    number: int,
    *,
    state: str = "OPEN",
    updated_at: str = "2026-08-01T00:00:00Z",
    closed_at: str | None = None,
    labels: list[str] | None = None,
    closed_by_commit: bool = False,
    blocked_by: list[BlockerFacts] | None = None,
    pulls: list[PullFacts] | None = None,
) -> IssueFacts:
    return {
        "number": number,
        "state": state,
        "updated_at": updated_at,
        "closed_at": closed_at,
        "labels": labels or [],
        "closed_by_commit": closed_by_commit,
        "blocked_by": blocked_by or [],
        "pulls": pulls or [],
    }


def _facts(*issues: IssueFacts) -> dict[int, IssueFacts]:
    return {issue["number"]: issue for issue in issues}


# ---------------------------------------------------------------------------
# linked_issue_numbers
# ---------------------------------------------------------------------------


def test_linked_issue_numbers_reads_the_bullets_when_frontmatter_is_empty() -> None:
    """Every issue lives in the section; frontmatter keeps at most the first."""
    assert linked_issue_numbers(frontmatter.loads(_BULLETS_MD)) == [1052, 1053, 1054, 1081]


def test_linked_issue_numbers_unions_the_frontmatter_issue_without_repeating_it() -> None:
    """github_issue is added only when the bullets do not already name it."""
    post = frontmatter.loads(_BULLETS_MD)
    post["github_issue"] = 1052
    assert linked_issue_numbers(post) == [1052, 1053, 1054, 1081]

    post["github_issue"] = 42
    assert linked_issue_numbers(post) == [1052, 1053, 1054, 1081, 42]


def test_linked_issue_numbers_of_a_placeholder_section_is_empty() -> None:
    """A freshly created requirement links nothing."""
    assert linked_issue_numbers(frontmatter.loads(_EMPTY_ISSUES_MD)) == []


def test_linked_issue_numbers_ignores_numbers_outside_a_bullet() -> None:
    """Prose mentions and cross-repo references are not this requirement's issues."""
    post = frontmatter.loads(_BULLETS_MD)
    post.content = post.content.replace(
        "- #1081 — Contest deadlines",
        "- owner/other#77 — Elsewhere\n  see #88 for context",
    )
    assert linked_issue_numbers(post) == [1052, 1053, 1054]


# ---------------------------------------------------------------------------
# derive — status
# ---------------------------------------------------------------------------


def test_derive_reports_done_when_every_issue_closed_with_a_merged_pr() -> None:
    """Closed issues carried by merged pull requests are shipped work."""
    facts = _facts(
        _issue(1, state="CLOSED", pulls=[_pull(10, state="MERGED", merged=True)]),
        _issue(2, state="CLOSED", pulls=[_pull(11, state="MERGED", merged=True)]),
    )

    result = derive("draft", [1, 2], facts, on_pr_branch=False)

    assert result["status"] == "done"
    assert result["source"] == "issues"
    assert result["signals"]["closed_without_merge"] is False


def test_derive_counts_a_closing_commit_as_landed() -> None:
    """A squash merge closes its issue through a commit, leaving no PR reference."""
    facts = _facts(_issue(115, state="CLOSED", closed_by_commit=True))

    result = derive("in-progress", [115], facts, on_pr_branch=False)

    assert result["status"] == "done"
    assert result["signals"]["closed_without_merge"] is False


def test_derive_flags_issues_closed_without_anything_merging() -> None:
    """All closed but nothing landed is dropped work, not finished work."""
    facts = _facts(
        _issue(1, state="CLOSED", pulls=[_pull(10, state="MERGED", merged=True)]),
        _issue(2, state="CLOSED", pulls=[_pull(11, state="CLOSED")]),
    )

    result = derive("in-progress", [1, 2], facts, on_pr_branch=False)

    assert result["status"] == "in-progress"
    assert result["signals"]["closed_without_merge"] is True


def test_derive_reports_idea_when_nothing_has_happened() -> None:
    """Open issues with no pull requests and no work label are untouched."""
    facts = _facts(_issue(1), _issue(2))

    result = derive("idea", [1, 2], facts, on_pr_branch=False)

    assert result["status"] == "idea"
    assert result["diverged"] is False


def test_derive_reports_in_progress_once_work_has_started() -> None:
    """An open pull request or the work label is movement on an open issue."""
    with_pull = _facts(_issue(1, pulls=[_pull(10)]), _issue(2))
    labelled = _facts(_issue(1, labels=["archon:in-progress"]), _issue(2))

    assert derive("idea", [1, 2], with_pull, on_pr_branch=False)["status"] == "in-progress"
    assert derive("idea", [1, 2], labelled, on_pr_branch=False)["status"] == "in-progress"


# ---------------------------------------------------------------------------
# derive — divergence
# ---------------------------------------------------------------------------


def test_derive_flags_a_draft_whose_work_already_shipped() -> None:
    """The requirement-030 case: the record says draft, the issues say done."""
    facts = _facts(_issue(1052, state="CLOSED", pulls=[_pull(1065, state="MERGED", merged=True)]))

    result = derive("draft", [1052], facts, on_pr_branch=False)

    assert result["status"] == "done"
    assert result["diverged"] is True


def test_derive_treats_planned_and_idea_as_the_same_progress() -> None:
    """Bookkeeping synonyms are not a disagreement about the work."""
    result = derive("planned", [1], _facts(_issue(1)), on_pr_branch=False)

    assert result["status"] == "idea"
    assert result["diverged"] is False


def test_derive_never_contradicts_a_cancelled_requirement() -> None:
    """Cancellation is author intent; the observation is reported, not enforced."""
    facts = _facts(_issue(1, state="CLOSED", pulls=[_pull(10, state="MERGED", merged=True)]))

    result = derive("cancelled", [1], facts, on_pr_branch=False)

    assert result["status"] == "done"
    assert result["diverged"] is False


def test_derive_flags_a_requirement_still_recorded_as_in_progress() -> None:
    """Nothing is left to do, but the record still claims work is under way."""
    facts = _facts(_issue(1, state="CLOSED", closed_by_commit=True))

    result = derive("in-progress", [1], facts, on_pr_branch=False)

    assert result["signals"]["closed_but_recorded_in_progress"] is True
    assert (
        derive("done", [1], facts, on_pr_branch=False)["signals"]["closed_but_recorded_in_progress"]
        is False
    )


# ---------------------------------------------------------------------------
# derive — signals and timestamps
# ---------------------------------------------------------------------------


def test_derive_reports_when_the_checks_started_failing() -> None:
    """The earliest failing check on an open pull request, not its own timestamp."""
    failing = _pull(
        10,
        updated_at="2026-09-05T12:00:00Z",
        checks=[
            {"conclusion": "SUCCESS", "at": "2026-08-30T21:00:00Z"},
            {"conclusion": "FAILURE", "at": "2026-08-30T22:30:46Z"},
            {"conclusion": "FAILURE", "at": "2026-09-05T11:00:00Z"},
        ],
    )

    result = derive("in-progress", [1], _facts(_issue(1, pulls=[failing])), on_pr_branch=False)

    assert result["signals"]["checks_failing_since"] == "2026-08-30T22:30:46Z"


def test_derive_reports_no_failing_checks_when_they_pass() -> None:
    """A green pull request is not a stuck one."""
    green = _pull(10, checks=[{"conclusion": "SUCCESS", "at": "2026-08-30T21:00:00Z"}])

    result = derive("in-progress", [1], _facts(_issue(1, pulls=[green])), on_pr_branch=False)

    assert result["signals"]["checks_failing_since"] is None


def test_derive_ignores_failing_checks_on_draft_and_closed_pull_requests() -> None:
    """Neither is work anyone is still trying to land."""
    broken: list[CheckFacts] = [{"conclusion": "FAILURE", "at": "2026-08-30T22:30:46Z"}]
    draft = _pull(10, is_draft=True, checks=broken)
    closed = _pull(11, state="CLOSED", checks=broken)

    result = derive(
        "in-progress", [1], _facts(_issue(1, pulls=[draft, closed])), on_pr_branch=False
    )

    assert result["signals"]["checks_failing_since"] is None


def test_derive_reports_the_latest_movement_on_issues_and_their_pull_requests() -> None:
    """Activity is what happened to the work, not when the file was last edited."""
    facts = _facts(
        _issue(
            1,
            updated_at="2026-08-01T00:00:00Z",
            pulls=[_pull(10, updated_at="2026-08-31T18:35:09Z")],
        ),
        _issue(2, updated_at="2026-08-15T00:00:00Z"),
    )

    result = derive("in-progress", [1, 2], facts, on_pr_branch=False)

    assert result["last_activity"] == "2026-08-31T18:35:09Z"


# ---------------------------------------------------------------------------
# derive — nothing to derive from
# ---------------------------------------------------------------------------


def test_derive_reports_a_requirement_with_no_issues_as_unlinked() -> None:
    """A requirement awaiting its own pull request is distinguished from one never linked."""
    on_main = derive("draft", [], {}, on_pr_branch=False)
    on_branch = derive("draft", [], {}, on_pr_branch=True)

    assert on_main["status"] is None
    assert on_main["source"] == "unlinked"
    assert on_main["diverged"] is False
    assert on_main["linked_issues"] == []
    assert on_main["signals"]["unlinked"] == "never_linked"
    assert on_branch["signals"]["unlinked"] == "awaiting_pr"


def test_derive_reports_unavailable_when_github_could_not_be_read() -> None:
    """No answer beats a confident wrong one; the issue numbers are still reported."""
    result = derive("draft", [1, 2], {}, on_pr_branch=False, failure="rate_limited")

    assert result["status"] is None
    assert result["source"] == "unavailable"
    assert result["unavailable_cause"] == "rate_limited"
    assert result["diverged"] is False
    assert result["linked_issues"] == [1, 2]


def test_derive_reports_unavailable_when_no_linked_issue_still_exists() -> None:
    """Deleted or transferred issues say nothing about the work — least of all 'idea'."""
    result = derive("draft", [1, 2], {}, on_pr_branch=False)

    assert result["status"] is None
    assert result["source"] == "unavailable"
    assert result["unavailable_cause"] == "issue_missing"


def test_derive_ignores_a_repo_failure_for_an_issue_that_came_back() -> None:
    """A batch that failed elsewhere must not unread the issues this one links."""
    result = derive("planned", [1], _facts(_issue(1)), on_pr_branch=False, failure="transient")

    assert result["source"] == "issues"
    assert result["unavailable_cause"] is None


def test_derive_uses_the_issues_that_do_exist() -> None:
    """One missing issue does not make the rest unreadable."""
    facts = _facts(_issue(1, state="CLOSED", closed_by_commit=True))

    result = derive("draft", [1, 2], facts, on_pr_branch=False)

    assert result["status"] == "done"
    assert result["linked_issues"] == [1, 2]


def test_derive_reports_when_the_work_landed() -> None:
    """The landing time is the latest merge across the requirement's pull requests."""
    facts = _facts(
        _issue(
            1,
            state="CLOSED",
            pulls=[_pull(500, state="MERGED", merged=True, merged_at="2026-08-20T10:00:00Z")],
        ),
        _issue(
            2,
            state="CLOSED",
            pulls=[_pull(501, state="MERGED", merged=True, merged_at="2026-09-01T09:00:00Z")],
        ),
    )

    result = derive("done", [1, 2], facts, on_pr_branch=False)

    assert result["status"] == "done"
    assert result["landed_at"] == "2026-09-01T09:00:00Z"


def test_derive_names_the_commit_that_finished_the_requirement() -> None:
    """The newest merge across the requirement's issues is the commit to ask about."""
    facts = _facts(
        _issue(
            1,
            state="CLOSED",
            pulls=[
                _pull(
                    500,
                    state="MERGED",
                    merged=True,
                    merged_at="2026-08-20T10:00:00Z",
                    merge_commit="older",
                )
            ],
        ),
        _issue(
            2,
            state="CLOSED",
            pulls=[
                _pull(
                    501,
                    state="MERGED",
                    merged=True,
                    merged_at="2026-09-01T09:00:00Z",
                    merge_commit="newest",
                )
            ],
        ),
    )

    assert derive("done", [1, 2], facts, on_pr_branch=False)["landed_commit"] == "newest"


def test_derive_names_the_last_merge_when_a_squash_close_landed_last() -> None:
    """A squash close names no pull request, so the newest merge answers for it."""
    facts = _facts(
        _issue(
            1,
            state="CLOSED",
            pulls=[
                _pull(
                    500,
                    state="MERGED",
                    merged=True,
                    merged_at="2026-08-20T10:00:00Z",
                    merge_commit="the-merge",
                )
            ],
        ),
        _issue(2, state="CLOSED", closed_at="2026-09-01T09:00:00Z", closed_by_commit=True),
    )

    result = derive("done", [1, 2], facts, on_pr_branch=False)

    assert result["landed_at"] == "2026-09-01T09:00:00Z"
    assert result["landed_commit"] == "the-merge"


def test_derive_names_no_commit_when_nothing_merged() -> None:
    """Every issue squash-closed: there is no merge commit to ask production about."""
    facts = _facts(
        _issue(1, state="CLOSED", closed_at="2026-08-20T10:00:00Z", closed_by_commit=True),
        _issue(2, state="CLOSED", closed_at="2026-09-01T09:00:00Z", closed_by_commit=True),
    )

    result = derive("done", [1, 2], facts, on_pr_branch=False)

    assert result["landed_at"] == "2026-09-01T09:00:00Z"
    assert result["landed_commit"] is None


def test_derive_names_no_commit_while_the_work_is_unfinished() -> None:
    facts = _facts(
        _issue(1, state="CLOSED", pulls=[_pull(500, state="MERGED", merged=True)]),
        _issue(2, state="OPEN"),
    )

    result = derive("in-progress", [1, 2], facts, on_pr_branch=False)

    assert result["status"] == "in-progress"
    assert result["landed_commit"] is None


def test_derive_falls_back_to_the_close_time_for_a_squash_commit() -> None:
    """An issue closed by a commit reports no pull request to read a merge time from."""
    facts = _facts(
        _issue(1, state="CLOSED", closed_at="2026-08-15T08:00:00Z", closed_by_commit=True)
    )

    result = derive("done", [1], facts, on_pr_branch=False)

    assert result["landed_at"] == "2026-08-15T08:00:00Z"


def test_derive_reports_no_landing_time_for_unfinished_work() -> None:
    """Nothing landed while an issue is still open."""
    facts = _facts(_issue(1), _issue(2, state="CLOSED", closed_by_commit=True))

    result = derive("in-progress", [1, 2], facts, on_pr_branch=False)

    assert result["status"] == "in-progress"
    assert result["landed_at"] is None


def test_derive_reports_the_open_blockers_of_open_issues() -> None:
    """What is holding the work up travels with the derivation, repo included."""
    blocker: BlockerFacts = {
        "repo": "owner/other",
        "number": 99,
        "last_activity": "2026-09-01T00:00:00Z",
    }
    facts = _facts(_issue(1, blocked_by=[blocker]))

    result = derive("in-progress", [1], facts, on_pr_branch=False)

    assert result["blocked_by"] == [blocker]


def test_derive_ignores_the_blockers_of_a_closed_issue() -> None:
    """A closed issue explains nothing about why the requirement is quiet."""
    blocker: BlockerFacts = {"repo": "owner/repo", "number": 99, "last_activity": None}
    facts = _facts(
        _issue(1, state="CLOSED", closed_by_commit=True, blocked_by=[blocker]), _issue(2)
    )

    result = derive("in-progress", [1, 2], facts, on_pr_branch=False)

    assert result["blocked_by"] == []


def test_derive_reports_a_shared_blocker_once() -> None:
    """Two linked issues held up by the same thing is still one blocker."""
    blocker: BlockerFacts = {"repo": "owner/repo", "number": 99, "last_activity": None}
    facts = _facts(_issue(1, blocked_by=[blocker]), _issue(2, blocked_by=[blocker]))

    result = derive("in-progress", [1, 2], facts, on_pr_branch=False)

    assert result["blocked_by"] == [blocker]
