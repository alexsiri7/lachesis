"""Unit tests for mcp/groups.py — the dashboard's nine requirement groups."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from groups import Candidate, group_requirements

_NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def _signals(
    *,
    closed_without_merge: bool = False,
    closed_but_recorded_in_progress: bool = False,
    checks_failing_since: str | None = None,
    unlinked: str | None = None,
) -> dict[str, Any]:
    return {
        "closed_without_merge": closed_without_merge,
        "closed_but_recorded_in_progress": closed_but_recorded_in_progress,
        "checks_failing_since": checks_failing_since,
        "unlinked": unlinked,
    }


def _derived(
    *,
    status: str | None = "in-progress",
    source: str = "issues",
    unavailable_cause: str | None = None,
    diverged: bool = False,
    last_activity: str | None = "2026-09-07T00:00:00Z",
    landed_at: str | None = None,
    linked_issues: list[int] | None = None,
    blocked_by: list[dict[str, Any]] | None = None,
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "unavailable_cause": unavailable_cause,
        "diverged": diverged,
        "last_activity": last_activity,
        "landed_at": landed_at,
        "linked_issues": linked_issues or [42],
        "blocked_by": blocked_by or [],
        "signals": signals or _signals(),
    }


def _candidate(
    req_id: str,
    *,
    repo: str = "owner/repo",
    status: str = "in-progress",
    derived: dict[str, Any] | None = None,
    **entry_fields: Any,
) -> Candidate:
    entry: dict[str, Any] = {
        "id": req_id,
        "title": f"Requirement {req_id}",
        "status": status,
        "github_issue": 42,
        **entry_fields,
    }
    if derived is not None:
        entry["derived"] = derived
    numbers = derived["linked_issues"] if derived else []
    return Candidate(repo, entry, numbers)


def _grouped(*candidates: Candidate, stale_after_days: int = 14) -> dict[str, list[dict[str, Any]]]:
    return group_requirements(list(candidates), now=_NOW, stale_after_days=stale_after_days)


def _blocker(*, number: int = 99, last_activity: str) -> dict[str, Any]:
    return {"repo": "owner/repo", "number": number, "last_activity": last_activity}


def _pr(number: int, *, created_at: str | None = "2026-09-01T00:00:00Z") -> dict[str, Any]:
    return {
        "number": number,
        "state": "open",
        "merged": False,
        "url": f"https://github.com/owner/repo/pull/{number}",
        "branch": f"lachesis/req-{number:03d}",
        "created_at": created_at,
    }


def test_landed_wins_over_diverged_for_a_lagging_record() -> None:
    """Work that shipped is reported as landed even when the file still says idea."""
    grouped = _grouped(
        _candidate(
            "001",
            status="idea",
            derived=_derived(status="done", diverged=True, landed_at="2026-09-01T10:00:00Z"),
        )
    )

    assert [entry["id"] for entry in grouped["landed"]] == ["001"]
    assert grouped["diverged"] == []
    assert grouped["landed"][0]["landed_at"] == "2026-09-01T10:00:00Z"


def test_cancelled_requirement_is_dropped_with_its_reason() -> None:
    """A cancelled requirement is dropped, carrying why, without any derivation."""
    grouped = _grouped(
        _candidate("002", status="cancelled", reason="Auth moved to the platform team")
    )

    assert [entry["id"] for entry in grouped["dropped"]] == ["002"]
    assert grouped["dropped"][0]["reason"] == "Auth moved to the platform team"
    assert grouped["dropped"][0]["derived_status"] is None


def test_a_spec_change_sorts_ahead_of_a_requirement_file_in_the_same_repo() -> None:
    spec_change = Candidate(
        "owner/repo",
        {
            "spec_change": 160,
            "title": "Spec change 160",
            "url": "https://github.com/owner/repo/pull/160",
            "dropped": True,
            "derived": _derived(),
        },
        [42],
    )

    grouped = _grouped(_candidate("002", status="cancelled"), spec_change)

    assert [entry.get("spec_change", entry.get("id")) for entry in grouped["dropped"]] == [
        160,
        "002",
    ]


def test_superseded_requirement_is_dropped_with_its_replacement() -> None:
    """A superseded requirement points at what replaced it."""
    replacement = {"repo": "owner/repo", "id": "030"}
    grouped = _grouped(_candidate("003", status="superseded", superseded_by=replacement))

    assert [entry["id"] for entry in grouped["dropped"]] == ["003"]
    assert grouped["dropped"][0]["superseded_by"] == replacement


def test_silence_past_the_threshold_is_stalled() -> None:
    """Nothing has moved for longer than the threshold allows."""
    grouped = _grouped(_candidate("004", derived=_derived(last_activity="2026-08-01T12:00:00Z")))

    assert [entry["id"] for entry in grouped["stalled"]] == ["004"]
    assert grouped["stalled"][0]["days_quiet"] == 38


def test_a_moving_blocker_explains_the_silence() -> None:
    """Being blocked behind live work is not stalling; the blocker is reported."""
    blocker = _blocker(last_activity="2026-09-06T12:00:00Z")
    grouped = _grouped(
        _candidate(
            "005",
            derived=_derived(last_activity="2026-08-01T12:00:00Z", blocked_by=[blocker]),
        )
    )

    assert grouped["stalled"] == []
    assert [entry["id"] for entry in grouped["in_flight"]] == ["005"]
    assert grouped["in_flight"][0]["blocked_by"] == [blocker]


def test_a_blocker_that_is_itself_quiet_explains_nothing() -> None:
    """Blocked behind something forgotten is still forgotten work."""
    grouped = _grouped(
        _candidate(
            "006",
            derived=_derived(
                last_activity="2026-08-01T12:00:00Z",
                blocked_by=[_blocker(last_activity="2026-07-01T12:00:00Z")],
            ),
        )
    )

    assert [entry["id"] for entry in grouped["stalled"]] == ["006"]


def test_two_signals_report_one_entry_carrying_both_kinds() -> None:
    """A requirement with several faults is still counted once."""
    grouped = _grouped(
        _candidate(
            "007",
            derived=_derived(
                signals=_signals(
                    closed_without_merge=True, checks_failing_since="2026-09-01T00:00:00Z"
                )
            ),
        )
    )

    assert [entry["id"] for entry in grouped["needs_attention"]] == ["007"]
    assert grouped["needs_attention"][0]["kinds"] == [
        "closed_without_merge",
        "checks_failing_since",
    ]


def test_an_unreadable_requirement_is_grouped_as_unreadable() -> None:
    """A requirement GitHub could not be read for is never reported as healthy.

    It is not a fault in the work either, so it answers to its own group rather
    than diluting the worklist.  The cause rides along: without it the one place
    a human reads "unavailable" cannot tell a spent rate limit from a repo the
    token lost access to.
    """
    grouped = _grouped(
        _candidate(
            "008",
            derived=_derived(
                status=None,
                source="unavailable",
                unavailable_cause="rate_limited",
                last_activity=None,
            ),
        )
    )

    assert [entry["id"] for entry in grouped["unreadable"]] == ["008"]
    assert grouped["unreadable"][0]["unavailable_cause"] == "rate_limited"
    assert grouped["needs_attention"] == []


def test_an_unreadable_requirement_quiet_for_months_is_unreadable_not_stalled() -> None:
    """Being unread outranks silence: the silence is not known to be the work's."""
    grouped = _grouped(
        _candidate(
            "008",
            derived=_derived(
                status=None,
                source="unavailable",
                unavailable_cause="rate_limited",
                last_activity="2026-01-01T00:00:00Z",
            ),
        )
    )

    assert [entry["id"] for entry in grouped["unreadable"]] == ["008"]
    assert grouped["stalled"] == []
    assert grouped["in_flight"] == []


def test_a_never_linked_requirement_quiet_for_months_is_never_linked_not_stalled() -> None:
    """Nobody has been silent about work nobody has started: it wants a decision, not a nudge."""
    grouped = _grouped(
        _candidate(
            "009",
            derived=_derived(
                status=None,
                source="unlinked",
                last_activity="2026-01-01T00:00:00Z",
                signals=_signals(unlinked="never_linked"),
            ),
        )
    )

    assert [entry["id"] for entry in grouped["never_linked"]] == ["009"]
    assert grouped["stalled"] == []
    assert grouped["in_flight"] == []


def test_an_unreadable_requirement_on_an_open_pr_awaits_its_merge() -> None:
    """The PR is the actionable fact, and the read failure rides along as its cause."""
    grouped = _grouped(
        _candidate(
            "008",
            pr=_pr(8),
            derived=_derived(
                status=None,
                source="unavailable",
                unavailable_cause="rate_limited",
                last_activity=None,
            ),
        )
    )

    assert [entry["id"] for entry in grouped["awaiting_merge"]] == ["008"]
    assert grouped["awaiting_merge"][0]["unavailable_cause"] == "rate_limited"
    assert "kinds" not in grouped["awaiting_merge"][0]


def test_a_requirement_awaiting_its_pr_is_not_one_that_was_never_linked() -> None:
    """The two want opposite responses: one wants a merge, the other a decision to start."""
    grouped = _grouped(
        _candidate(
            "009",
            derived=_derived(
                status=None, source="unlinked", signals=_signals(unlinked="never_linked")
            ),
        ),
        _candidate(
            "010",
            pr=_pr(10),
            derived=_derived(
                status=None, source="unlinked", signals=_signals(unlinked="awaiting_pr")
            ),
        ),
    )

    assert [entry["id"] for entry in grouped["never_linked"]] == ["009"]
    assert [entry["id"] for entry in grouped["awaiting_merge"]] == ["010"]
    assert grouped["needs_attention"] == []


def test_a_recent_divergence_with_no_fault_is_diverged() -> None:
    """The record disagrees with the work, and that is all that is wrong."""
    grouped = _grouped(_candidate("011", status="idea", derived=_derived(diverged=True)))

    assert [entry["id"] for entry in grouped["diverged"]] == ["011"]


def test_recent_and_healthy_is_in_flight() -> None:
    """Nothing to report means the work is simply under way."""
    grouped = _grouped(_candidate("012", derived=_derived()))

    assert [entry["id"] for entry in grouped["in_flight"]] == ["012"]


def test_every_group_is_present_and_empty_for_no_candidates() -> None:
    """Empty groups are reported as empty rather than omitted."""
    grouped = _grouped()

    assert grouped == {
        "landed": [],
        "dropped": [],
        "awaiting_merge": [],
        "stalled": [],
        "needs_attention": [],
        "unreadable": [],
        "never_linked": [],
        "diverged": [],
        "in_flight": [],
    }


def test_every_requirement_is_counted_exactly_once() -> None:
    """Overlapping conditions still partition the corpus."""
    candidates = [
        _candidate("001", status="idea", derived=_derived(status="done", diverged=True)),
        _candidate("002", status="cancelled", reason="Dropped"),
        _candidate(
            "003",
            derived=_derived(
                diverged=True,
                last_activity="2026-01-01T00:00:00Z",
                signals=_signals(closed_without_merge=True),
            ),
        ),
        _candidate("004", derived=_derived(diverged=True, last_activity="2026-01-01T00:00:00Z")),
        _candidate("005", status="idea", derived=_derived(diverged=True)),
        _candidate("006", derived=_derived()),
        _candidate("007", derived=_derived(status=None, source="unavailable", last_activity=None)),
        _candidate(
            "008",
            derived=_derived(
                status=None,
                source="unlinked",
                last_activity=None,
                signals=_signals(unlinked="never_linked"),
            ),
        ),
    ]

    grouped = group_requirements(candidates, now=_NOW, stale_after_days=14)

    found = [entry["id"] for group in grouped.values() for entry in group]
    assert sorted(found) == ["001", "002", "003", "004", "005", "006", "007", "008"]
    assert sum(len(group) for group in grouped.values()) == len(candidates)


def test_stalled_lists_the_longest_quiet_first() -> None:
    """The longest-quiet item is the most likely to have been forgotten."""
    grouped = _grouped(
        _candidate("001", derived=_derived(last_activity="2026-08-01T12:00:00Z")),
        _candidate("002", derived=_derived(last_activity="2026-05-01T12:00:00Z")),
        _candidate("003", derived=_derived(last_activity="2026-07-01T12:00:00Z")),
    )

    assert [entry["id"] for entry in grouped["stalled"]] == ["002", "003", "001"]


def test_landed_lists_the_most_recent_first() -> None:
    """What shipped last is what a reader is asking about."""
    grouped = _grouped(
        _candidate("001", derived=_derived(status="done", landed_at="2026-06-01T00:00:00Z")),
        _candidate("002", derived=_derived(status="done", landed_at="2026-09-01T00:00:00Z")),
        _candidate("003", derived=_derived(status="done", landed_at=None)),
    )

    assert [entry["id"] for entry in grouped["landed"]] == ["002", "001", "003"]


def test_an_unparseable_timestamp_is_never_stalled() -> None:
    """One malformed timestamp must not cost the caller the dashboard."""
    grouped = _grouped(_candidate("013", derived=_derived(last_activity="not a timestamp")))

    assert grouped["stalled"] == []
    assert grouped["in_flight"][0]["days_quiet"] is None


def test_silence_of_exactly_the_threshold_is_stalled() -> None:
    """The threshold is the first day that counts, not the last that does not."""
    grouped = _grouped(
        _candidate("014", derived=_derived(last_activity="2026-08-25T12:00:00Z")),
        stale_after_days=14,
    )

    assert [entry["id"] for entry in grouped["stalled"]] == ["014"]
    assert grouped["stalled"][0]["days_quiet"] == 14


def test_needs_attention_leads_with_the_likeliest_failure_then_the_quietest() -> None:
    """Kind orders the group; how long it has been broken orders each kind."""
    grouped = _grouped(
        _candidate(
            "001",
            derived=_derived(
                last_activity="2026-09-03T12:00:00Z",
                signals=_signals(checks_failing_since="2026-09-03T12:00:00Z"),
            ),
        ),
        _candidate(
            "002",
            derived=_derived(
                last_activity="2026-09-07T12:00:00Z",
                signals=_signals(closed_without_merge=True),
            ),
        ),
        _candidate(
            "003",
            derived=_derived(
                last_activity="2026-07-20T12:00:00Z",
                signals=_signals(checks_failing_since="2026-07-20T12:00:00Z"),
            ),
        ),
    )

    assert [(entry["id"], entry["days_quiet"]) for entry in grouped["needs_attention"]] == [
        ("002", 1),
        ("003", 50),
        ("001", 5),
    ]


# ---------------------------------------------------------------------------
# awaiting_merge
# ---------------------------------------------------------------------------


def test_a_requirement_whose_file_is_still_on_a_pr_is_not_reported_as_landed() -> None:
    """Its issues shipped, but the requirement itself is stuck behind an unmerged PR."""
    grouped = _grouped(
        _candidate(
            "020",
            pr=_pr(20),
            derived=_derived(status="done", landed_at="2026-09-01T10:00:00Z"),
        )
    )

    assert grouped["landed"] == []
    assert [entry["id"] for entry in grouped["awaiting_merge"]] == ["020"]
    assert grouped["awaiting_merge"][0]["pr"]["number"] == 20


def test_awaiting_merge_still_names_the_fault_it_also_has() -> None:
    """Saying what to do about the file must not lose what is wrong with the work."""
    grouped = _grouped(
        _candidate(
            "021",
            pr=_pr(21),
            derived=_derived(signals=_signals(checks_failing_since="2026-09-01T00:00:00Z")),
        )
    )

    assert [entry["id"] for entry in grouped["awaiting_merge"]] == ["021"]
    assert grouped["awaiting_merge"][0]["kinds"] == ["checks_failing_since"]


def test_a_cancelled_requirement_on_a_pr_is_still_dropped() -> None:
    """A cancelled requirement's open PR wants closing, not merging."""
    grouped = _grouped(_candidate("022", status="cancelled", pr=_pr(22), derived=_derived()))

    assert grouped["awaiting_merge"] == []
    assert [entry["id"] for entry in grouped["dropped"]] == ["022"]


def test_awaiting_merge_leads_with_the_oldest_pr_across_repos() -> None:
    """The oldest unmerged requirement PR is the one most likely forgotten."""
    grouped = _grouped(
        _candidate("023", repo="owner/other", pr=_pr(3, created_at="2026-08-20T00:00:00Z")),
        _candidate("024", pr=_pr(24, created_at="2026-07-01T00:00:00Z")),
        _candidate("025", pr=_pr(25, created_at=None)),
        _candidate("026", pr=_pr(26, created_at="2026-09-05T00:00:00Z")),
    )

    assert [entry["id"] for entry in grouped["awaiting_merge"]] == ["024", "023", "026", "025"]


def test_a_requirement_on_the_default_branch_is_untouched_by_the_new_group() -> None:
    """Requirements whose file already landed on main group exactly as before."""
    grouped = _grouped(_candidate("027", pr=None, derived=_derived(diverged=True)))

    assert grouped["awaiting_merge"] == []
    assert [entry["id"] for entry in grouped["diverged"]] == ["027"]
