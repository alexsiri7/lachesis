"""Tests for mcp/web.py — cookie-based session auth and web dashboard routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport
from jose import jwt as jose_jwt
from starlette.requests import Request  # noqa: TCH002

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> object:
    from main import app as _app

    return _app


# ---------------------------------------------------------------------------
# _get_web_user unit tests
# ---------------------------------------------------------------------------


def test_get_web_user_rejects_wrong_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.settings.secret_key", "secret")
    monkeypatch.setattr("web.settings.allowed_email", "allowed@example.com")
    from web import _get_web_user

    token = jose_jwt.encode({"sub": "x", "email": "attacker@evil.com"}, "secret", algorithm="HS256")
    scope = {
        "type": "http",
        "headers": [(b"cookie", f"lachesis_web_session={token}".encode())],
    }
    result = _get_web_user(Request(scope))
    assert result is None


def test_get_web_user_accepts_correct_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.settings.secret_key", "secret")
    monkeypatch.setattr("web.settings.allowed_email", "allowed@example.com")
    from web import _get_web_user

    token = jose_jwt.encode(
        {"sub": "x", "email": "allowed@example.com"}, "secret", algorithm="HS256"
    )
    scope = {
        "type": "http",
        "headers": [(b"cookie", f"lachesis_web_session={token}".encode())],
    }
    result = _get_web_user(Request(scope))
    assert result is not None
    assert result["email"] == "allowed@example.com"


def test_get_web_user_returns_none_when_no_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("web.settings.secret_key", "")
    from web import _get_web_user

    scope = {
        "type": "http",
        "headers": [(b"cookie", b"lachesis_web_session=anything")],
    }
    assert _get_web_user(Request(scope)) is None


def test_get_web_user_returns_none_when_no_token() -> None:
    from web import _get_web_user

    scope = {"type": "http", "headers": []}
    assert _get_web_user(Request(scope)) is None


# ---------------------------------------------------------------------------
# web_index route tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_index_redirects_to_login_without_session(app: object) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.get("/web/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/login"


@pytest.mark.asyncio
async def test_web_index_redirects_to_login_for_wrong_email(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.settings.secret_key", "secret")
    monkeypatch.setattr("web.settings.allowed_email", "allowed@example.com")
    token = jose_jwt.encode(
        {"sub": "x", "email": "attacker@evil.com", "lachesis_uid": "uid"},
        "secret",
        algorithm="HS256",
    )
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        cookies={"lachesis_web_session": token},
    ) as client:
        resp = await client.get("/web/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/login"


@pytest.mark.asyncio
async def test_web_index_redirects_when_no_lachesis_uid(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """JWT with correct email but no lachesis_uid should redirect to login."""
    monkeypatch.setattr("web.settings.secret_key", "secret")
    monkeypatch.setattr("web.settings.allowed_email", "allowed@example.com")
    # JWT has correct email but no lachesis_uid claim
    token = jose_jwt.encode(
        {"sub": "x", "email": "allowed@example.com"},
        "secret",
        algorithm="HS256",
    )
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        cookies={"lachesis_web_session": token},
    ) as client:
        resp = await client.get("/web/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/login"


# ---------------------------------------------------------------------------
# web_callback CSRF state check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_callback_state_mismatch_redirects_to_login(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.settings.mcp_base_url", "https://lachesis.example.com")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        cookies={"lachesis_oauth_state": "correct-state"},
    ) as client:
        resp = await client.get(
            "/web/callback?code=abc&state=wrong-state", follow_redirects=False
        )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/login"


@pytest.mark.asyncio
async def test_web_callback_missing_code_redirects_to_login(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.settings.mcp_base_url", "https://lachesis.example.com")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        cookies={"lachesis_oauth_state": "state123"},
    ) as client:
        resp = await client.get("/web/callback?state=state123", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/login"


@pytest.mark.asyncio
async def test_web_callback_happy_path_sets_session_cookie(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("web.settings.mcp_base_url", "https://lachesis.example.com")
    monkeypatch.setattr("web.settings.secret_key", "secret")
    with (
        patch(
            "web.exchange_google_code",
            new=AsyncMock(return_value={"id": "gid", "email": "allowed@example.com"}),
        ),
        patch("web.db.upsert_user", return_value={"id": "db-uid"}),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
            cookies={"lachesis_oauth_state": "state123"},
        ) as client:
            resp = await client.get(
                "/web/callback?code=auth-code&state=state123", follow_redirects=False
            )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/"
    assert "lachesis_web_session" in resp.cookies


@pytest.mark.asyncio
async def test_web_callback_upsert_failure_redirects_to_login(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When upsert_user fails, callback should redirect to login (fail fast)."""
    monkeypatch.setattr("web.settings.mcp_base_url", "https://lachesis.example.com")
    monkeypatch.setattr("web.settings.secret_key", "secret")
    with (
        patch(
            "web.exchange_google_code",
            new=AsyncMock(return_value={"id": "gid", "email": "allowed@example.com"}),
        ),
        patch("web.db.upsert_user", side_effect=RuntimeError("Supabase down")),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
            cookies={"lachesis_oauth_state": "state123"},
        ) as client:
            resp = await client.get(
                "/web/callback?code=auth-code&state=state123", follow_redirects=False
            )
    assert resp.status_code == 302
    assert "/web/login" in resp.headers["location"]


# ---------------------------------------------------------------------------
# web_logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_logout_clears_session_cookie(app: object) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
        cookies={"lachesis_web_session": "sometoken"},
    ) as client:
        resp = await client.get("/web/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/web/login"
    # Cookie should be cleared (httpx represents deleted cookies as empty string)
    assert resp.cookies.get("lachesis_web_session", "") == ""
