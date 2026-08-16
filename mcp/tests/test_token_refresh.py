"""Integration tests for proactive token refresh in the auth middleware."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport
from jose import jwt as jose_jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

SECRET = "test-secret"


def _make_expired_jwt(
    sub: str = "google-sub",
    email: str = "user@example.com",
    lachesis_uid: str | None = None,
) -> str:
    payload: dict[str, object] = {"sub": sub, "email": email, "exp": int(time.time()) - 1}
    if lachesis_uid is not None:
        payload["lachesis_uid"] = lachesis_uid
    return str(jose_jwt.encode(payload, SECRET, algorithm="HS256"))


def _make_valid_jwt(
    sub: str = "google-sub",
    email: str = "user@example.com",
    lachesis_uid: str | None = "db-uuid-123",
) -> str:
    payload: dict[str, object] = {
        "sub": sub,
        "email": email,
        "exp": int(time.time()) + 3600,
    }
    if lachesis_uid is not None:
        payload["lachesis_uid"] = lachesis_uid
    return str(jose_jwt.encode(payload, SECRET, algorithm="HS256"))


async def _echo_handler(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _make_test_app():
    """Create a simple Starlette app wrapped with the auth middleware for testing."""
    from main import with_user_context

    inner = Starlette(routes=[Route("/mcp", _echo_handler)])
    return with_user_context(inner)


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("auth.settings.secret_key", SECRET)
    monkeypatch.setattr("auth.settings.allowed_email", "user@example.com")
    monkeypatch.setattr("main.settings.mcp_base_url", "https://lachesis.example.com")
    monkeypatch.setattr("main.settings.secret_key", SECRET)
    monkeypatch.setattr("main.settings.allowed_email", "user@example.com")
    # Reset startup check flag between tests
    monkeypatch.setattr("main._startup_check_done", True)


@pytest.fixture()
def app():
    return _make_test_app()


@pytest.mark.asyncio
async def test_expired_token_with_valid_refresh_token_returns_200(app: object) -> None:
    token = _make_expired_jwt(lachesis_uid="db-uuid-123")
    rt_data = ("rt-token-abc", {"user_id": "google-sub", "lachesis_uid": "db-uuid-123"})
    with patch("main._db.get_refresh_token_by_user_id", return_value=rt_data):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/mcp",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert resp.status_code == 200
    assert "x-lachesis-access-token" in resp.headers
    # Verify the new token is a valid JWT
    new_token = resp.headers["x-lachesis-access-token"]
    payload = jose_jwt.decode(new_token, SECRET, algorithms=["HS256"])
    assert payload["sub"] == "google-sub"


@pytest.mark.asyncio
async def test_expired_token_with_no_refresh_token_returns_401_with_actionable_message(
    app: object,
) -> None:
    token = _make_expired_jwt()
    with patch("main._db.get_refresh_token_by_user_id", return_value=None):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/mcp",
                headers={"Authorization": f"Bearer {token}"},
            )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "token_expired"
    assert "re-authenticate" in body["description"].lower()


@pytest.mark.asyncio
async def test_invalid_token_returns_generic_401(app: object) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/mcp",
            headers={"Authorization": "Bearer not.a.valid.jwt"},
        )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "unauthorized"


@pytest.mark.asyncio
async def test_valid_token_bypasses_refresh_logic(app: object) -> None:
    token = _make_valid_jwt()
    with patch("main._db.get_refresh_token_by_user_id") as mock_rt:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/mcp",
                headers={"Authorization": f"Bearer {token}"},
            )
    # Refresh token lookup should never be called for valid tokens
    mock_rt.assert_not_called()
    assert resp.status_code == 200
    assert "x-lachesis-access-token" not in resp.headers


# ---------------------------------------------------------------------------
# Startup health check tests
# ---------------------------------------------------------------------------


def test_startup_check_logs_warning_when_no_tokens(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with patch("main._db.list_active_refresh_tokens", return_value=[]):
        from main import _check_refresh_token_health

        with caplog.at_level(logging.WARNING):
            _check_refresh_token_health()
    assert "no active refresh tokens found" in caplog.text


def test_startup_check_logs_warning_when_expiring_soon(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    expiring_token = {
        "token": "rt-soon",
        "data": {"user_id": "uid"},
        "expires_at": datetime.now(UTC) + timedelta(days=2),
    }
    with patch("main._db.list_active_refresh_tokens", return_value=[expiring_token]):
        from main import _check_refresh_token_health

        with caplog.at_level(logging.WARNING):
            _check_refresh_token_health()
    assert "expiring within" in caplog.text


def test_startup_check_logs_info_when_healthy(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    healthy_token = {
        "token": "rt-healthy",
        "data": {"user_id": "uid"},
        "expires_at": datetime.now(UTC) + timedelta(days=20),
    }
    with patch("main._db.list_active_refresh_tokens", return_value=[healthy_token]):
        from main import _check_refresh_token_health

        with caplog.at_level(logging.INFO):
            _check_refresh_token_health()
    assert "all healthy" in caplog.text
