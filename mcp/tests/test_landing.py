"""Tests for mcp/landing.py — deciding which units of work have just landed.

The landing-notifications capability's scenarios each have a test named after
them, run for both kinds of unit of work; see
``openspec/specs/landing-notifications/spec.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

import github
import landing
from auth import current_db_user_id
from derive import IssueFacts, derive
from tools.specs import _spec_change_entry

_EPOCH = datetime(2026, 9, 1, tzinfo=UTC)
_CONFIG = {
    "server": "https://ntfy.example",
    "topic": "lachesis",
    "token": "",
    "created_at": _EPOCH,
}

_WHAT = """\
---
id: '026'
title: Push notification when a requirement lands
---

## Why

Work arrives silently.

## What

**The trigger**

The author receives a push notification when a requirement reaches production.
"""


@pytest.fixture()
def set_user_id():
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


def _candidate(
    req_id: str = "026",
    *,
    status: str = "in-progress",
    derived_status: str | None = "done",
    landed_at: str | None = "2026-09-05T10:00:00Z",
    landed_commit: str | None = "abc123",
    content: str = _WHAT,
) -> landing.Candidate:
    entry: dict[str, Any] = {
        "id": req_id,
        "title": "Push notification when a requirement lands",
        "status": status,
        "derived": {
            "status": derived_status,
            "landed_at": landed_at,
            "landed_commit": landed_commit,
        },
    }
    return landing.Candidate(f"requirements/{req_id}-push.md", content, entry)


@pytest.fixture()
def configured():
    """A configured account with no requirement notified yet, on the merge signal."""
    with (
        patch("landing.db.get_ntfy_config", return_value=dict(_CONFIG)) as config,
        patch("landing.db.notified_requirement_ids", return_value=set()) as notified,
        patch("landing.db.get_landing_signal", return_value="merge") as signal,
        patch("landing.notify.notify_requirement", new_callable=AsyncMock) as sent,
    ):
        yield {"config": config, "notified": notified, "signal": signal, "sent": sent}


# ---------------------------------------------------------------------------
# summarize_what
# ---------------------------------------------------------------------------


def test_summarize_what_skips_a_bold_lead_in() -> None:
    summary = landing.summarize_what(_WHAT, "fallback")
    assert summary.startswith("The author receives a push notification")


def test_summarize_what_truncates_at_a_word_boundary() -> None:
    long_line = " ".join(["capability"] * 40)
    summary = landing.summarize_what(f"## What\n\n{long_line}\n", "fallback")

    assert summary.endswith("...")
    assert len(summary) <= landing._SUMMARY_MAX_CHARS + 3
    assert "capabilit..." not in summary


def test_summarize_what_falls_back_to_the_title() -> None:
    assert landing.summarize_what("## Why\n\nNo what section.\n", "The title") == "The title"


# ---------------------------------------------------------------------------
# gating
# ---------------------------------------------------------------------------


async def test_no_config_sends_nothing(set_user_id: str) -> None:
    with (
        patch("landing.db.get_ntfy_config", return_value=None),
        patch("landing.notify.notify_requirement", new_callable=AsyncMock) as sent,
    ):
        await landing.notify_landed("owner/repo", [_candidate()])
    sent.assert_not_awaited()


async def test_empty_topic_sends_nothing(set_user_id: str) -> None:
    with (
        patch("landing.db.get_ntfy_config", return_value={**_CONFIG, "topic": ""}),
        patch("landing.notify.notify_requirement", new_callable=AsyncMock) as sent,
    ):
        await landing.notify_landed("owner/repo", [_candidate()])
    sent.assert_not_awaited()


async def test_landing_before_the_epoch_is_history(set_user_id: str, configured: dict) -> None:
    await landing.notify_landed("owner/repo", [_candidate(landed_at="2026-08-20T10:00:00Z")])
    configured["sent"].assert_not_awaited()


async def test_landing_after_the_epoch_notifies(set_user_id: str, configured: dict) -> None:
    await landing.notify_landed("owner/repo", [_candidate()])
    configured["sent"].assert_awaited_once()


async def test_already_notified_is_not_notified_again(set_user_id: str, configured: dict) -> None:
    configured["notified"].return_value = {"026"}
    await landing.notify_landed("owner/repo", [_candidate()])
    configured["sent"].assert_not_awaited()


async def test_partial_completion_is_not_notified(set_user_id: str, configured: dict) -> None:
    await landing.notify_landed(
        "owner/repo", [_candidate(derived_status="in-progress", landed_at=None)]
    )
    configured["sent"].assert_not_awaited()


async def test_cancelled_requirement_is_not_announced(set_user_id: str, configured: dict) -> None:
    """Its issues merging does not make it a capability to go and use."""
    await landing.notify_landed("owner/repo", [_candidate(status="cancelled")])
    configured["sent"].assert_not_awaited()


async def test_a_raising_db_call_is_swallowed(set_user_id: str) -> None:
    with patch("landing.db.get_ntfy_config", side_effect=RuntimeError("no db")):
        assert await landing.notify_landed("owner/repo", [_candidate()]) is None


# ---------------------------------------------------------------------------
# the notification itself
# ---------------------------------------------------------------------------


async def test_merge_signal_says_merged_not_deployed(set_user_id: str, configured: dict) -> None:
    await landing.notify_landed("owner/repo", [_candidate()])

    kwargs = configured["sent"].await_args.kwargs
    assert kwargs["title"] == "Push notification when a requirement lands (owner/repo)"
    assert "Merged to the default branch" in kwargs["body"]
    assert kwargs["click"] == "https://github.com/owner/repo/blob/HEAD/requirements/026-push.md"
    assert kwargs["tags"] == ["rocket"]
    assert kwargs["requirement_id"] == "026"
    assert kwargs["priority"] is None


async def test_deployment_signal_success_says_deployed(set_user_id: str, configured: dict) -> None:
    configured["signal"].return_value = "deployment"
    with patch(
        "landing.github.get_production_deployment",
        new_callable=AsyncMock,
        return_value={"state": "success", "url": "https://example/log"},
    ) as deployment:
        await landing.notify_landed("owner/repo", [_candidate()])

    deployment.assert_awaited_once_with("owner/repo", "abc123")
    kwargs = configured["sent"].await_args.kwargs
    assert "Deployed to production." in kwargs["body"]
    assert kwargs["priority"] is None


async def test_deployment_signal_failure_links_the_deployment(
    set_user_id: str, configured: dict
) -> None:
    configured["signal"].return_value = "deployment"
    with patch(
        "landing.github.get_production_deployment",
        new_callable=AsyncMock,
        return_value={"state": "failure", "url": "https://example/failed-log"},
    ):
        await landing.notify_landed("owner/repo", [_candidate()])

    kwargs = configured["sent"].await_args.kwargs
    assert kwargs["title"].startswith("Failed to deploy: ")
    assert kwargs["click"] == "https://example/failed-log"
    assert kwargs["tags"] == ["rotating_light"]
    assert kwargs["priority"] == "high"


async def test_pending_deployment_takes_no_claim(set_user_id: str, configured: dict) -> None:
    """Nothing is spent while a deployment is still moving, so a later read still notifies."""
    configured["signal"].return_value = "deployment"
    with (
        patch(
            "landing.github.get_production_deployment",
            new_callable=AsyncMock,
            return_value={"state": "pending", "url": None},
        ),
        patch("landing.db.claim_notification") as claim,
    ):
        await landing.notify_landed("owner/repo", [_candidate()])

    configured["sent"].assert_not_awaited()
    claim.assert_not_called()


async def test_unreadable_deployment_takes_no_claim(set_user_id: str, configured: dict) -> None:
    configured["signal"].return_value = "deployment"
    with (
        patch(
            "landing.github.get_production_deployment",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("landing.db.claim_notification") as claim,
    ):
        await landing.notify_landed("owner/repo", [_candidate()])

    configured["sent"].assert_not_awaited()
    claim.assert_not_called()


async def test_deployment_signal_without_a_merge_commit_announces_the_merge(
    set_user_id: str, configured: dict
) -> None:
    configured["signal"].return_value = "deployment"
    with patch("landing.github.get_production_deployment", new_callable=AsyncMock) as deployment:
        await landing.notify_landed("owner/repo", [_candidate(landed_commit=None)])

    deployment.assert_not_awaited()
    assert "Merged to the default branch" in configured["sent"].await_args.kwargs["body"]


# ---------------------------------------------------------------------------
# summarize_description
# ---------------------------------------------------------------------------


def test_summarize_description_takes_the_first_sentence() -> None:
    description = "## Why\n\n**Context**\n\nSpec changes now\nnotify when they land. More detail.\n"
    assert (
        landing.summarize_description(description, "fallback")
        == "Spec changes now notify when they land."
    )


def test_summarize_description_without_a_full_stop_keeps_the_paragraph() -> None:
    assert landing.summarize_description("Notify on landing\n\nLater.", "t") == "Notify on landing"


def test_summarize_description_falls_back_to_the_title() -> None:
    assert landing.summarize_description("", "The title") == "The title"


# ---------------------------------------------------------------------------
# The landing-notifications scenarios, for both kinds of unit of work
# ---------------------------------------------------------------------------

_SPEC_DESCRIPTION = "Spec changes notify the author when they land. They are a unit of work."
_SPEC_URL = "https://github.com/owner/repo/pull/152"

UnitOfWork = Callable[..., landing.Landable]


def _issue(number: int, *, landed: bool) -> IssueFacts:
    """An issue closed by a merged pull request, or still open with none."""
    return {
        "number": number,
        "state": "CLOSED" if landed else "OPEN",
        "updated_at": "2026-09-05T10:00:00Z",
        "closed_at": "2026-09-05T10:00:00Z" if landed else None,
        "labels": [],
        "closed_by_commit": False,
        "blocked_by": [],
        "pulls": [
            {
                "number": 900 + number,
                "state": "MERGED",
                "merged": True,
                "updated_at": f"2026-09-05T10:00:{number % 60:02d}Z",
                "merged_at": f"2026-09-05T10:00:{number % 60:02d}Z",
                "merge_commit": f"commit{number}",
                "is_draft": False,
                "checks": [],
            }
        ]
        if landed
        else [],
    }


def _requirement_file(issues: int, landed: int) -> landing.Candidate:
    numbers = list(range(1, issues + 1))
    facts = {n: _issue(n, landed=n <= landed) for n in numbers}
    return landing.Candidate(
        "requirements/026-push.md",
        _WHAT,
        {
            "id": "026",
            "title": "Push notification when a requirement lands",
            "status": "in-progress",
            "derived": derive("in-progress", numbers, facts, on_pr_branch=False),
        },
    )


def _spec_change(issues: int, landed: int) -> landing.SpecChangeCandidate:
    numbers = list(range(1, issues + 1))
    facts = {n: _issue(n, landed=n <= landed) for n in numbers}
    pull: github.IssueSummary = {
        "number": 152,
        "title": "Landing notifications for spec changes",
        "url": _SPEC_URL,
        "body": _SPEC_DESCRIPTION,
        "state": "closed",
        "labels": ["spec-change"],
        "is_pull_request": True,
        "merged": True,
        "created_at": "2026-09-02T10:00:00Z",
        "updated_at": "2026-09-03T10:00:00Z",
    }
    return landing.SpecChangeCandidate(pull["body"], _spec_change_entry(pull, numbers, facts, None))


@pytest.fixture(params=["requirement_file", "spec_change"])
def unit_of_work(request: pytest.FixtureRequest) -> UnitOfWork:
    return {"requirement_file": _requirement_file, "spec_change": _spec_change}[request.param]


async def test_last_issue_lands(
    set_user_id: str, configured: dict, unit_of_work: UnitOfWork
) -> None:
    await landing.notify_landed("owner/repo", [unit_of_work(issues=3, landed=3)])

    configured["sent"].assert_awaited_once()
    kwargs = configured["sent"].await_args.kwargs
    assert "owner/repo" in kwargs["title"]
    assert kwargs["tags"] == ["rocket"]


async def test_last_issue_lands_names_the_spec_change_and_links_its_pull_request(
    set_user_id: str, configured: dict
) -> None:
    await landing.notify_landed("owner/repo", [_spec_change(issues=3, landed=3)])

    kwargs = configured["sent"].await_args.kwargs
    assert kwargs["title"] == "Landing notifications for spec changes (owner/repo)"
    assert kwargs["body"].startswith("Spec changes notify the author when they land.\n\n")
    assert "They are a unit of work." not in kwargs["body"]
    assert kwargs["click"] == _SPEC_URL
    assert kwargs["requirement_id"] == "spec:152"


async def test_partial_landing(
    set_user_id: str, configured: dict, unit_of_work: UnitOfWork
) -> None:
    await landing.notify_landed("owner/repo", [unit_of_work(issues=4, landed=3)])
    configured["sent"].assert_not_awaited()


async def test_spec_change_with_no_issues_never_notifies(
    set_user_id: str, configured: dict
) -> None:
    await landing.notify_landed("owner/repo", [_spec_change(issues=0, landed=0)])
    configured["sent"].assert_not_awaited()


async def test_deployment_signal(
    set_user_id: str, configured: dict, unit_of_work: UnitOfWork
) -> None:
    configured["signal"].return_value = "deployment"
    candidate = unit_of_work(issues=3, landed=3)
    deployment = AsyncMock(return_value={"state": "pending", "url": None})
    with patch("landing.github.get_production_deployment", deployment):
        await landing.notify_landed("owner/repo", [candidate])
    configured["sent"].assert_not_awaited()

    deployment.return_value = {"state": "success", "url": "https://example/log"}
    with patch("landing.github.get_production_deployment", deployment):
        await landing.notify_landed("owner/repo", [candidate])

    deployment.assert_awaited_with("owner/repo", "commit3")
    configured["sent"].assert_awaited_once()
    assert "Deployed to production." in configured["sent"].await_args.kwargs["body"]


async def test_failed_deploy(set_user_id: str, configured: dict, unit_of_work: UnitOfWork) -> None:
    configured["signal"].return_value = "deployment"
    candidate = unit_of_work(issues=3, landed=3)
    with patch(
        "landing.github.get_production_deployment",
        new_callable=AsyncMock,
        return_value={"state": "failure", "url": "https://example/failed-log"},
    ):
        await landing.notify_landed("owner/repo", [candidate])

    kwargs = configured["sent"].await_args.kwargs
    assert kwargs["title"].startswith("Failed to deploy: ")
    assert f"The deployment carrying this {candidate.kind} failed." in kwargs["body"]
    assert kwargs["click"] == "https://example/failed-log"
    assert kwargs["priority"] == "high"


async def test_redeploy(set_user_id: str, configured: dict, unit_of_work: UnitOfWork) -> None:
    candidate = unit_of_work(issues=3, landed=3)
    configured["notified"].return_value = {candidate.key}
    await landing.notify_landed("owner/repo", [candidate])
    configured["sent"].assert_not_awaited()


async def test_redeploy_of_a_requirement_does_not_silence_a_spec_change_numbered_alike(
    set_user_id: str, configured: dict
) -> None:
    configured["notified"].return_value = {"152"}
    await landing.notify_landed("owner/repo", [_spec_change(issues=3, landed=3)])
    configured["sent"].assert_awaited_once()


async def test_notification_service_down(
    set_user_id: str, httpx_mock: HTTPXMock, unit_of_work: UnitOfWork
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("unreachable"))
    with (
        patch("landing.db.get_ntfy_config", return_value=dict(_CONFIG)),
        patch("landing.db.notified_requirement_ids", return_value=set()),
        patch("landing.db.get_landing_signal", return_value="merge"),
        patch("landing.db.claim_notification", return_value=True) as claim,
    ):
        assert await landing.notify_landed("owner/repo", [unit_of_work(issues=3, landed=3)]) is None

    claim.assert_called_once()
    assert len(httpx_mock.get_requests()) == 1


async def test_no_topic_configured(set_user_id: str, unit_of_work: UnitOfWork) -> None:
    with (
        patch("landing.db.get_ntfy_config", return_value={**_CONFIG, "topic": ""}),
        patch("landing.notify.notify_requirement", new_callable=AsyncMock) as sent,
    ):
        await landing.notify_landed("owner/repo", [unit_of_work(issues=3, landed=3)])
    sent.assert_not_awaited()
