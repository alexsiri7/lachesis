"""Unit tests for the audit due rules in tools.audits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from tools.audits import (
    _due,
    _last_audited,
    _merged_since,
    _next_capability,
    _open_findings,
    next_audit,
)

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


def test_open_findings_are_the_last_runs_issues_still_open() -> None:
    listing = [{"number": 40, "state": "open"}, {"number": 41, "state": "closed"}]

    assert _open_findings(_run(3, issues=[40, 41, 42]), listing) == [40]
    assert _open_findings(None, listing) == []


def test_last_audited_is_the_last_successful_run_of_the_capability() -> None:
    runs = [
        _run(9, capability="fuel"),
        _run(4, capability="fuel"),
        _run(1, capability="fuel", outcome="failed"),
    ]

    assert _last_audited("fuel", runs) == (NOW - timedelta(days=4)).isoformat()
    assert _last_audited("backlog", runs) is None


def _due_audit(repo: str, kind: str, estimate: int | None = None) -> dict:
    return {
        "repo": repo,
        "kind": kind,
        "capability": None,
        "estimate_tokens": estimate,
        "confidence": "low",
        "samples": 0,
    }


def _history(due: list[dict], open_audits: dict[str, int | None]) -> dict:
    return {
        "model": "claude",
        "repos": [{"repo": repo, "audits": [], "open_audit": n} for repo, n in open_audits.items()],
        "due": due,
        "unreadable": [],
    }


def _budget(left_tokens: int, *, within_pace: bool | None = True) -> dict:
    return {"left_tokens": left_tokens, "within_pace": within_pace}


async def _next(history: dict, repo: str | None, budget: dict | None) -> dict | None:
    with patch("tools.audits.audit_history", AsyncMock(return_value=history)):
        return await next_audit("test-uid", repo, budget)


async def test_next_audit_skips_a_repository_whose_audit_issue_is_open() -> None:
    history = _history(
        [_due_audit("owner/a", "security"), _due_audit("owner/b", "tests")],
        {"owner/a": 50, "owner/b": None},
    )

    assert await _next(history, None, None) == {**_due_audit("owner/b", "tests"), "model": "claude"}
    assert await _next(history, "owner/a", None) is None


async def test_next_audit_offers_only_the_named_repository() -> None:
    history = _history(
        [_due_audit("owner/a", "security"), _due_audit("owner/b", "tests")],
        {"owner/a": None, "owner/b": None},
    )

    offered = await _next(history, "owner/b", None)

    assert offered is not None and (offered["repo"], offered["kind"]) == ("owner/b", "tests")


async def test_next_audit_passes_over_an_audit_too_big_for_the_budget_left() -> None:
    history = _history(
        [_due_audit("owner/a", "security", 80), _due_audit("owner/a", "tests", 20)],
        {"owner/a": None},
    )

    offered = await _next(history, None, _budget(50))

    assert offered is not None and offered["kind"] == "tests"
    assert await _next(history, None, _budget(0)) is None


async def test_next_audit_waits_while_spending_is_ahead_of_pace() -> None:
    history = _history([_due_audit("owner/a", "security", 10)], {"owner/a": None})

    assert await _next(history, None, _budget(100, within_pace=False)) is None
