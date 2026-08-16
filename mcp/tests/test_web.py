"""Unit tests for mcp/web.py — _extract_section helper and session auth."""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport
from jose import jwt as jose_jwt

# Ensure mcp/ is on the path so we can import web directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web import _COOKIE_NAME, _extract_section, _get_web_user_status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_SECRET = "test-secret"
_TEST_EMAIL = "user@example.com"


def _make_jwt(email: str, exp_delta_seconds: int = 3600) -> str:
    """Build a real signed JWT suitable for testing."""
    exp = int(time.time()) + exp_delta_seconds
    payload = {"sub": "google-sub", "email": email, "exp": exp}
    return str(jose_jwt.encode(payload, _TEST_SECRET, algorithm="HS256"))


def _make_request(cookie_value: str | None) -> object:
    """Build a minimal fake request with the session cookie set."""
    req = MagicMock()
    req.cookies = {_COOKIE_NAME: cookie_value} if cookie_value else {}
    return req


# ---------------------------------------------------------------------------
# _get_web_user_status unit tests
# ---------------------------------------------------------------------------


def test_get_web_user_status_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.settings.secret_key", _TEST_SECRET)
    monkeypatch.setattr("web.settings.allowed_email", _TEST_EMAIL)
    token = _make_jwt(_TEST_EMAIL)
    status, payload = _get_web_user_status(_make_request(token))
    assert status == "ok"
    assert payload is not None
    assert payload["email"] == _TEST_EMAIL


def test_get_web_user_status_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.settings.secret_key", _TEST_SECRET)
    monkeypatch.setattr("web.settings.allowed_email", _TEST_EMAIL)
    token = _make_jwt(_TEST_EMAIL, exp_delta_seconds=-1)
    status, payload = _get_web_user_status(_make_request(token))
    assert status == "expired"
    assert payload is None


def test_get_web_user_status_missing_no_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.settings.secret_key", _TEST_SECRET)
    monkeypatch.setattr("web.settings.allowed_email", _TEST_EMAIL)
    status, payload = _get_web_user_status(_make_request(None))
    assert status == "missing"
    assert payload is None


def test_get_web_user_status_missing_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.settings.secret_key", _TEST_SECRET)
    monkeypatch.setattr("web.settings.allowed_email", _TEST_EMAIL)
    status, payload = _get_web_user_status(_make_request("not.a.valid.jwt"))
    assert status == "missing"
    assert payload is None


def test_get_web_user_status_missing_wrong_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.settings.secret_key", _TEST_SECRET)
    monkeypatch.setattr("web.settings.allowed_email", "other@example.com")
    token = _make_jwt(_TEST_EMAIL)
    status, payload = _get_web_user_status(_make_request(token))
    assert status == "missing"
    assert payload is None


# ---------------------------------------------------------------------------
# web_index expired-session route tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> object:
    from main import app as _app

    return _app


@pytest.mark.asyncio
async def test_web_index_expired_session_redirects_and_clears_cookie(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expired session on GET /web/ must redirect to login?reason=expired and clear the cookie."""
    monkeypatch.setattr("web.settings.secret_key", _TEST_SECRET)
    monkeypatch.setattr("web.settings.allowed_email", _TEST_EMAIL)
    expired_token = _make_jwt(_TEST_EMAIL, exp_delta_seconds=-1)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        client.cookies.set(_COOKIE_NAME, expired_token)
        resp = await client.get("/web/")

    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/login?reason=expired"
    # Cookie should be cleared (Set-Cookie with max-age=0 or past expiry date)
    set_cookie = resp.headers.get("set-cookie", "")
    assert _COOKIE_NAME in set_cookie
    assert "max-age=0" in set_cookie.lower() or "expires=" in set_cookie.lower()


# ---------------------------------------------------------------------------
# web_login anti-loop guard and expired banner tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_login_expired_session_shows_login_page_not_redirect(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expired session must NOT redirect from login page — that would cause an infinite loop."""
    monkeypatch.setattr("web.settings.secret_key", _TEST_SECRET)
    monkeypatch.setattr("web.settings.allowed_email", _TEST_EMAIL)
    monkeypatch.setattr("web.settings.mcp_base_url", "http://test")
    expired_token = _make_jwt(_TEST_EMAIL, exp_delta_seconds=-1)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        client.cookies.set(_COOKIE_NAME, expired_token)
        resp = await client.get("/web/login?reason=expired")

    assert resp.status_code == 200
    assert "Reconnect with Google" in resp.text
    assert "session has expired" in resp.text.lower()


@pytest.mark.asyncio
async def test_web_login_no_session_shows_default_sign_in(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No session renders the login page with the default 'Sign in with Google' button."""
    monkeypatch.setattr("web.settings.secret_key", _TEST_SECRET)
    monkeypatch.setattr("web.settings.allowed_email", _TEST_EMAIL)
    monkeypatch.setattr("web.settings.mcp_base_url", "http://test")

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        resp = await client.get("/web/login")

    assert resp.status_code == 200
    assert "Sign in with Google" in resp.text
    assert "session has expired" not in resp.text.lower()


def test_extracts_section_in_middle():
    body = "## Why\nBecause users need it.\n## What\nA button."
    assert _extract_section(body, "Why") == "Because users need it."


def test_extracts_last_section():
    body = "## Why\nFirst reason.\n## What\nThe deliverable."
    assert _extract_section(body, "What") == "The deliverable."


def test_returns_empty_string_when_section_missing():
    body = "## Why\nSome reason."
    assert _extract_section(body, "What") == ""


def test_returns_empty_string_for_empty_body():
    assert _extract_section("", "Why") == ""


def test_returns_empty_string_for_none_body():
    """post.content can be None when a .md file has no body."""
    assert _extract_section(None, "Why") == ""  # type: ignore[arg-type]


def test_strips_trailing_whitespace():
    body = "## Why\n  Leading and trailing.  \n## What\nX."
    assert _extract_section(body, "Why") == "Leading and trailing."


def test_multiline_section_content():
    body = "## Why\nLine one.\nLine two.\n## What\nX."
    result = _extract_section(body, "Why")
    assert "Line one." in result
    assert "Line two." in result


def test_section_at_start_of_body():
    """No preceding content before first ## — ^ anchor must fire."""
    body = "## Why\nReason here."
    assert _extract_section(body, "Why") == "Reason here."


def test_special_regex_chars_in_heading():
    """re.escape must handle headings containing special regex characters."""
    body = "## Why (v2)\nReason."
    assert _extract_section(body, "Why (v2)") == "Reason."


def test_crlf_line_endings_are_normalized():
    """GitHub files pushed from Windows may have CRLF line endings."""
    body = "## Why\r\nReason with CRLF.\r\n## What\r\nDeliverable."
    assert _extract_section(body, "Why") == "Reason with CRLF."


def test_crlf_last_section():
    body = "## Why\r\nFirst reason.\r\n## What\r\nThe deliverable."
    assert _extract_section(body, "What") == "The deliverable."
