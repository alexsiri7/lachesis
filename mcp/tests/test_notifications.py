"""Tests for mcp/tools/notifications.py — ntfy configuration MCP tools."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from auth import current_db_user_id
from tools.notifications import send_test_notification, set_ntfy_config


@pytest.fixture()
def set_user_id():
    """Set current_db_user_id for the test, reset afterwards."""
    token = current_db_user_id.set("test-uid")
    yield "test-uid"
    current_db_user_id.reset(token)


@pytest.fixture()
def clear_user_id():
    """Ensure current_db_user_id returns None (its default)."""
    token = current_db_user_id.set(None)
    yield
    current_db_user_id.reset(token)


@pytest.fixture()
def unconfigured():
    """Report that the account has never been configured."""
    with patch("tools.notifications.db.get_ntfy_config", return_value=None) as mock:
        yield mock


# ---------------------------------------------------------------------------
# set_ntfy_config
# ---------------------------------------------------------------------------


async def test_set_ntfy_config_stores_topic(set_user_id: str, unconfigured: object) -> None:
    stored = {"server": "https://ntfy.sh", "topic": "lachesis", "token": ""}
    with patch("tools.notifications.db.set_ntfy_config", return_value=stored) as mock:
        result = await set_ntfy_config("lachesis")

    mock.assert_called_once_with("test-uid", "https://ntfy.sh", "lachesis", "")
    assert result == {"server": "https://ntfy.sh", "topic": "lachesis", "token_set": False}


async def test_set_ntfy_config_never_returns_the_token(
    set_user_id: str, unconfigured: object
) -> None:
    stored = {"server": "https://ntfy.sh", "topic": "lachesis", "token": "tk_secret"}
    with patch("tools.notifications.db.set_ntfy_config", return_value=stored):
        result = await set_ntfy_config("lachesis", token="tk_secret")

    assert result["token_set"] is True
    assert "tk_secret" not in str(result)


async def test_set_ntfy_config_clears_with_empty_topic(
    set_user_id: str, unconfigured: object
) -> None:
    stored = {"server": "https://ntfy.sh", "topic": "", "token": ""}
    with patch("tools.notifications.db.set_ntfy_config", return_value=stored) as mock:
        result = await set_ntfy_config("")

    mock.assert_called_once_with("test-uid", "https://ntfy.sh", "", "")
    assert result["topic"] == ""


async def test_set_ntfy_config_trims_whitespace(set_user_id: str, unconfigured: object) -> None:
    stored = {"server": "https://n.example", "topic": "lachesis", "token": ""}
    with patch("tools.notifications.db.set_ntfy_config", return_value=stored) as mock:
        await set_ntfy_config("  lachesis  ", server="  https://n.example  ")

    mock.assert_called_once_with("test-uid", "https://n.example", "lachesis", "")


async def test_set_ntfy_config_keeps_the_server_and_token_it_was_not_given(
    set_user_id: str,
) -> None:
    """Renaming a topic must not reset the server or drop the token protecting it."""
    existing = {"server": "https://n.example", "topic": "old", "token": "tk_secret"}
    stored = dict(existing, topic="renamed")
    with patch("tools.notifications.db.get_ntfy_config", return_value=existing):
        with patch("tools.notifications.db.set_ntfy_config", return_value=stored) as mock:
            result = await set_ntfy_config("renamed")

    mock.assert_called_once_with("test-uid", "https://n.example", "renamed", "tk_secret")
    assert result == {"server": "https://n.example", "topic": "renamed", "token_set": True}


async def test_set_ntfy_config_clears_the_token_when_given_an_empty_one(
    set_user_id: str,
) -> None:
    """Leaving a value alone is the omitted case; an empty string still clears it."""
    existing = {"server": "https://n.example", "topic": "lachesis", "token": "tk_secret"}
    stored = dict(existing, token="")
    with patch("tools.notifications.db.get_ntfy_config", return_value=existing) as read:
        with patch("tools.notifications.db.set_ntfy_config", return_value=stored) as mock:
            result = await set_ntfy_config("lachesis", token="")

    read.assert_called_once_with("test-uid")
    mock.assert_called_once_with("test-uid", "https://n.example", "lachesis", "")
    assert result["token_set"] is False


async def test_set_ntfy_config_does_not_read_the_row_when_given_every_field(
    set_user_id: str,
) -> None:
    stored = {"server": "https://n.example", "topic": "lachesis", "token": "tk"}
    with patch("tools.notifications.db.get_ntfy_config") as read:
        with patch("tools.notifications.db.set_ntfy_config", return_value=stored):
            await set_ntfy_config("lachesis", server="https://n.example", token="tk")

    read.assert_not_called()


async def test_set_ntfy_config_raises_when_no_user(clear_user_id: None) -> None:
    with pytest.raises(RuntimeError, match="No DB user ID in request context"):
        await set_ntfy_config("lachesis")


# ---------------------------------------------------------------------------
# send_test_notification
# ---------------------------------------------------------------------------


async def test_send_test_notification_reports_delivery(set_user_id: str) -> None:
    config = {"server": "https://ntfy.sh", "topic": "lachesis", "token": ""}
    with patch("tools.notifications.db.get_ntfy_config", return_value=config):
        with patch("tools.notifications.notify.send", return_value=True) as send:
            result = await send_test_notification()

    assert result["sent"] is True
    assert result["topic"] == "lachesis"
    assert send.call_args.kwargs["title"] == "Lachesis test notification"


async def test_send_test_notification_reports_no_topic(set_user_id: str) -> None:
    with patch("tools.notifications.db.get_ntfy_config", return_value=None):
        with patch("tools.notifications.notify.send") as send:
            result = await send_test_notification()

    assert result["sent"] is False
    assert "no topic configured" in result["reason"]
    send.assert_not_called()


async def test_send_test_notification_reports_failed_delivery(set_user_id: str) -> None:
    config = {"server": "https://ntfy.sh", "topic": "lachesis", "token": ""}
    with patch("tools.notifications.db.get_ntfy_config", return_value=config):
        with patch("tools.notifications.notify.send", return_value=False):
            result = await send_test_notification()

    assert result["sent"] is False
    assert "delivery failed" in result["reason"]


async def test_send_test_notification_bypasses_the_dedupe_record(set_user_id: str) -> None:
    """The test hook exists to be run repeatedly, so it must never claim a requirement."""
    config = {"server": "https://ntfy.sh", "topic": "lachesis", "token": ""}
    with patch("tools.notifications.db.get_ntfy_config", return_value=config):
        with patch("tools.notifications.notify.send", return_value=True):
            with patch("db.claim_notification") as claim:
                await send_test_notification()
                await send_test_notification()

    claim.assert_not_called()


async def test_send_test_notification_raises_when_no_user(clear_user_id: None) -> None:
    with pytest.raises(RuntimeError, match="No DB user ID in request context"):
        await send_test_notification()
