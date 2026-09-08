"""Tests for mcp/notify.py — best-effort ntfy delivery and per-requirement dedupe."""

from __future__ import annotations

import logging
from unittest.mock import patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

import notify

_CONFIG = {"server": "https://ntfy.example", "topic": "lachesis", "token": ""}


@pytest.fixture()
def configured():
    """Report a configured topic for every get_ntfy_config call."""
    with patch("notify.db.get_ntfy_config", return_value=dict(_CONFIG)) as mock:
        yield mock


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


async def test_send_posts_to_server_and_topic(configured: object, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response()
    delivered = await notify.send("uid", title="Landed", body="Go and try it")

    assert delivered is True
    request = httpx_mock.get_requests()[0]
    assert str(request.url) == "https://ntfy.example/lachesis"
    assert request.headers["Title"] == "Landed"
    assert request.content == b"Go and try it"


async def test_send_carries_click_and_tags(configured: object, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response()
    await notify.send(
        "uid",
        title="Landed",
        body="body",
        click="https://github.com/owner/repo/blob/main/requirements/026-x.md",
        tags=["rocket", "tada"],
    )

    request = httpx_mock.get_requests()[0]
    assert request.headers["Click"].endswith("requirements/026-x.md")
    assert request.headers["Tags"] == "rocket,tada"


async def test_send_authenticates_when_token_configured(httpx_mock: HTTPXMock) -> None:
    config = dict(_CONFIG, token="tk_secret")
    httpx_mock.add_response()
    with patch("notify.db.get_ntfy_config", return_value=config):
        await notify.send("uid", title="Landed", body="body")

    assert httpx_mock.get_requests()[0].headers["Authorization"] == "Bearer tk_secret"


async def test_send_encodes_non_ascii_title(configured: object, httpx_mock: HTTPXMock) -> None:
    """An em dash in a requirement title must not break the request in httpx."""
    httpx_mock.add_response()
    delivered = await notify.send("uid", title="Push notification — landed", body="body")

    assert delivered is True
    assert httpx_mock.get_requests()[0].headers["Title"] == (
        "=?utf-8?q?Push_notification_=E2=80=94_landed?="
    )


async def test_send_does_not_fold_a_long_non_ascii_title(
    configured: object, httpx_mock: HTTPXMock
) -> None:
    """RFC 2047 encoding folds past 78 chars by default, and a newline breaks the header."""
    httpx_mock.add_response()
    title = "Push notification when a requirement lands in production — landed"
    await notify.send("uid", title=title, body="b")

    assert "\n" not in httpx_mock.get_requests()[0].headers["Title"]


async def test_send_tolerates_trailing_slash_on_server(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response()
    with patch(
        "notify.db.get_ntfy_config", return_value=dict(_CONFIG, server="https://n.example/")
    ):
        await notify.send("uid", title="t", body="b")

    assert str(httpx_mock.get_requests()[0].url) == "https://n.example/lachesis"


# --- no topic configured ---------------------------------------------------


async def test_send_is_inert_when_no_topic(httpx_mock: HTTPXMock) -> None:
    with patch("notify.db.get_ntfy_config", return_value=dict(_CONFIG, topic="")):
        delivered = await notify.send("uid", title="t", body="b")

    assert delivered is False
    assert httpx_mock.get_requests() == []


async def test_send_is_inert_when_never_configured(httpx_mock: HTTPXMock) -> None:
    with patch("notify.db.get_ntfy_config", return_value=None):
        delivered = await notify.send("uid", title="t", body="b")

    assert delivered is False
    assert httpx_mock.get_requests() == []


# --- failures never reach the caller ---------------------------------------


async def test_send_logs_and_drops_unreachable_endpoint(
    configured: object, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("no route to host"))
    with caplog.at_level(logging.WARNING, logger="notify"):
        delivered = await notify.send("uid", title="t", body="b")

    assert delivered is False
    assert "delivery to https://ntfy.example/lachesis failed" in caplog.text


async def test_send_logs_and_drops_error_status(
    configured: object, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    httpx_mock.add_response(status_code=500)
    with caplog.at_level(logging.WARNING, logger="notify"):
        delivered = await notify.send("uid", title="t", body="b")

    assert delivered is False
    assert "failed" in caplog.text


async def test_send_drops_unreadable_config(
    httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    with patch("notify.db.get_ntfy_config", side_effect=RuntimeError("no DB")):
        with caplog.at_level(logging.WARNING, logger="notify"):
            delivered = await notify.send("uid", title="t", body="b")

    assert delivered is False
    assert httpx_mock.get_requests() == []
    assert "could not read ntfy config" in caplog.text


# ---------------------------------------------------------------------------
# notify_requirement
# ---------------------------------------------------------------------------


async def test_notify_requirement_delivers_on_first_claim(
    configured: object, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response()
    with patch("notify.db.claim_notification", return_value=True) as claim:
        delivered = await notify.notify_requirement(
            "uid", repo="owner/repo", requirement_id="026", title="Landed", body="body"
        )

    assert delivered is True
    claim.assert_called_once_with("uid", "owner/repo", "026")
    assert len(httpx_mock.get_requests()) == 1


async def test_notify_requirement_suppresses_second_attempt(
    configured: object, httpx_mock: HTTPXMock
) -> None:
    """A requirement already claimed must not produce a second request."""
    with patch("notify.db.claim_notification", return_value=False):
        delivered = await notify.notify_requirement(
            "uid", repo="owner/repo", requirement_id="026", title="Landed", body="body"
        )

    assert delivered is False
    assert httpx_mock.get_requests() == []


async def test_notify_requirement_normalizes_repo_for_the_dedupe_key(
    configured: object, httpx_mock: HTTPXMock
) -> None:
    """A URL and a bare owner/repo are the same requirement, so one key covers both."""
    httpx_mock.add_response()
    with patch("notify.db.claim_notification", return_value=True) as claim:
        await notify.notify_requirement(
            "uid",
            repo="https://github.com/owner/repo",
            requirement_id="026",
            title="Landed",
            body="body",
        )

    claim.assert_called_once_with("uid", "owner/repo", "026")


async def test_notify_requirement_drops_unparseable_repo(httpx_mock: HTTPXMock) -> None:
    with patch("notify.db.claim_notification") as claim:
        delivered = await notify.notify_requirement(
            "uid", repo="not-a-repo", requirement_id="026", title="t", body="b"
        )

    assert delivered is False
    claim.assert_not_called()
    assert httpx_mock.get_requests() == []


async def test_notify_requirement_drops_when_claim_fails(
    configured: object, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    with patch("notify.db.claim_notification", side_effect=RuntimeError("no DB")):
        with caplog.at_level(logging.WARNING, logger="notify"):
            delivered = await notify.notify_requirement(
                "uid", repo="owner/repo", requirement_id="026", title="t", body="b"
            )

    assert delivered is False
    assert httpx_mock.get_requests() == []
    assert "could not claim notification" in caplog.text


async def test_notify_requirement_keeps_the_claim_when_delivery_succeeds(
    configured: object, httpx_mock: HTTPXMock
) -> None:
    """Only a failed send gives the claim back — a delivered one is what suppresses the rest."""
    httpx_mock.add_response()
    with patch("notify.db.claim_notification", return_value=True) as claim:
        with patch("notify.db.release_notification") as release:
            await notify.notify_requirement(
                "uid", repo="owner/repo", requirement_id="026", title="t", body="b"
            )

    claim.assert_called_once()
    release.assert_not_called()


async def test_notify_requirement_releases_the_claim_when_delivery_fails(
    configured: object, httpx_mock: HTTPXMock
) -> None:
    """A transient ntfy outage must leave the requirement retryable, not look delivered."""
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    with patch("notify.db.claim_notification", return_value=True):
        with patch("notify.db.release_notification") as release:
            delivered = await notify.notify_requirement(
                "uid", repo="owner/repo", requirement_id="026", title="t", body="b"
            )

    assert delivered is False
    release.assert_called_once_with("uid", "owner/repo", "026")


async def test_notify_requirement_survives_a_failed_release(
    configured: object, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("boom"))
    with patch("notify.db.claim_notification", return_value=True):
        with patch("notify.db.release_notification", side_effect=RuntimeError("no DB")):
            with caplog.at_level(logging.WARNING, logger="notify"):
                delivered = await notify.notify_requirement(
                    "uid", repo="owner/repo", requirement_id="026", title="t", body="b"
                )

    assert delivered is False
    assert "could not release the claim" in caplog.text
