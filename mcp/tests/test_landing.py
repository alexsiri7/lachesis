"""Tests for mcp/landing.py — deciding which requirements have just landed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import landing
from auth import current_db_user_id

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
