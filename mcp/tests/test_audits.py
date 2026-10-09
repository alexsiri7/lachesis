"""Unit tests for the audit due rules in tools.audits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from tools.audits import _due, _merged_since, _next_capability

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def _run(days_ago: float, *, model: str = "claude", outcome: str = "clean", **extra: Any) -> dict:
    return {
        "finished_at": NOW - timedelta(days=days_ago),
        "model": model,
        "outcome": outcome,
        "capability": None,
        **extra,
    }


def _pull(days_ago: float, *, merged: bool = True) -> dict:
    return {
        "is_pull_request": True,
        "merged": merged,
        "closed_at": (NOW - timedelta(days=days_ago)).isoformat(),
    }


def test_never_run_is_due() -> None:
    assert _due("tests", [], [], "claude", NOW) == (True, 0)


def test_last_run_failed_is_due() -> None:
    assert _due("tests", [_run(1, outcome="failed")], [], "claude", NOW)[0] is True


def test_merges_since_the_last_successful_run_make_it_due() -> None:
    listing = [_pull(5), _pull(1), _pull(0.5, merged=False)]

    assert _due("tests", [_run(2)], listing, "claude", NOW) == (True, 1)


def test_quiet_repository_is_not_due() -> None:
    assert _due("architecture", [_run(2)], [_pull(5)], "claude", NOW) == (False, 0)


def test_a_new_auditor_model_makes_it_due() -> None:
    assert _due("architecture", [_run(2, model="older")], [], "claude", NOW)[0] is True


def test_security_falls_due_after_sixty_days_even_when_quiet() -> None:
    assert _due("security", [_run(61)], [], "claude", NOW)[0] is True
    assert _due("security", [_run(59)], [], "claude", NOW)[0] is False
    assert _due("tests", [_run(61)], [], "claude", NOW)[0] is False


def test_merged_since_counts_only_merged_pull_requests_after_the_run() -> None:
    listing = [_pull(3), _pull(1), _pull(1, merged=False), {"is_pull_request": False}]

    assert _merged_since(listing, NOW - timedelta(days=2)) == 1
    assert _merged_since(listing, None) == 2


def test_next_capability_prefers_never_audited_then_least_recent() -> None:
    runs = [_run(9, capability="sprints"), _run(4, capability="fuel")]

    assert _next_capability(["backlog", "fuel", "sprints"], runs) == "backlog"
    assert _next_capability(["fuel", "sprints"], runs) == "sprints"
    assert _next_capability([], runs) is None
