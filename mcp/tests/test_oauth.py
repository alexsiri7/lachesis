"""Tests for mcp/oauth.py — exchange_google_code helper and OAuth routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from httpx import ASGITransport
from jose import jwt as jose_jwt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_id_token(
    email: str,
    sub: str = "uid123",
    email_verified: bool = True,
) -> str:
    """Build a minimal unsigned id_token for testing.

    ``jwt.get_unverified_claims`` does not verify the signature, so any secret works.
    """
    payload = {"email": email, "sub": sub, "email_verified": email_verified}
    return str(jose_jwt.encode(payload, "test-secret", algorithm="HS256"))


# ---------------------------------------------------------------------------
# exchange_google_code unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exchange_google_code_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oauth.settings.google_client_id", "cid")
    monkeypatch.setattr("oauth.settings.google_client_secret", "csecret")
    monkeypatch.setattr("oauth.settings.allowed_email", "allowed@example.com")

    from oauth import exchange_google_code

    with respx.mock:
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200, json={"id_token": _make_id_token("allowed@example.com")}
            )
        )
        result = await exchange_google_code("authcode", "https://server/oauth/callback")

    assert result == {"id": "uid123", "email": "allowed@example.com"}


@pytest.mark.asyncio
async def test_exchange_google_code_rejects_wrong_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oauth.settings.google_client_id", "cid")
    monkeypatch.setattr("oauth.settings.google_client_secret", "csecret")
    monkeypatch.setattr("oauth.settings.allowed_email", "allowed@example.com")

    from oauth import exchange_google_code

    with respx.mock:
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(200, json={"id_token": _make_id_token("attacker@evil.com")})
        )
        result = await exchange_google_code("authcode", "https://server/oauth/callback")

    assert result is None


@pytest.mark.asyncio
async def test_exchange_google_code_rejects_unverified_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("oauth.settings.google_client_id", "cid")
    monkeypatch.setattr("oauth.settings.google_client_secret", "csecret")
    monkeypatch.setattr("oauth.settings.allowed_email", "allowed@example.com")

    from oauth import exchange_google_code

    with respx.mock:
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(
                200,
                json={"id_token": _make_id_token("allowed@example.com", email_verified=False)},
            )
        )
        result = await exchange_google_code("authcode", "https://server/oauth/callback")

    assert result is None


@pytest.mark.asyncio
async def test_exchange_google_code_non200_returns_none() -> None:
    from oauth import exchange_google_code

    with respx.mock:
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        result = await exchange_google_code("badcode", "https://server/oauth/callback")

    assert result is None


@pytest.mark.asyncio
async def test_exchange_google_code_missing_id_token_returns_none() -> None:
    from oauth import exchange_google_code

    with respx.mock:
        respx.post("https://oauth2.googleapis.com/token").mock(
            return_value=httpx.Response(200, json={"access_token": "tok"})
        )
        result = await exchange_google_code("code", "https://server/oauth/callback")

    assert result is None


@pytest.mark.asyncio
async def test_exchange_google_code_network_error_returns_none() -> None:
    from oauth import exchange_google_code

    with respx.mock:
        respx.post("https://oauth2.googleapis.com/token").mock(
            side_effect=httpx.ConnectError("unreachable")
        )
        result = await exchange_google_code("code", "https://server/oauth/callback")

    assert result is None


# ---------------------------------------------------------------------------
# oauth_callback route integration tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> object:
    from main import app as _app

    return _app


@pytest.fixture()
def seeded_session() -> str:
    """Pre-seed a valid OAuth session and return the flow_key."""
    from oauth_state import cleanup_and_store, oauth_sessions

    flow_key = "testflowkey1234567890abcdefghij"
    cleanup_and_store(
        oauth_sessions,
        flow_key,
        {
            "client_id": "test-client",
            "redirect_uri": "https://claude.ai/callback",
            "client_state": "clientstate123",
            "code_challenge": "challenge",
            "code_challenge_method": "S256",
            "scope": "mcp",
        },
    )
    return flow_key


@pytest.mark.asyncio
async def test_callback_missing_code_returns_400(app: object) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",  # type: ignore[arg-type]
    ) as client:
        resp = await client.get("/oauth/callback?state=somestate")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_google_error_returns_400(app: object) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",  # type: ignore[arg-type]
    ) as client:
        resp = await client.get("/oauth/callback?error=access_denied&state=somestate")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_invalid_state_returns_400(app: object) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",  # type: ignore[arg-type]
    ) as client:
        resp = await client.get("/oauth/callback?code=gc&state=nonexistent_state_xyz")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_callback_failed_exchange_returns_403(
    app: object, seeded_session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oauth.settings.mcp_base_url", "https://lachesis.example.com")
    with patch("oauth.exchange_google_code", new=AsyncMock(return_value=None)):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(f"/oauth/callback?code=badcode&state={seeded_session}")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_callback_happy_path_redirects(
    app: object, seeded_session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oauth.settings.mcp_base_url", "https://lachesis.example.com")
    with patch(
        "oauth.exchange_google_code",
        new=AsyncMock(return_value={"id": "uid123", "email": "allowed@example.com"}),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(f"/oauth/callback?code=gc&state={seeded_session}")
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert "https://claude.ai/callback" in loc
    assert "code=" in loc
    assert "state=clientstate123" in loc


# ---------------------------------------------------------------------------
# oauth_authorize route tests
# ---------------------------------------------------------------------------

_AUTHORIZE_PARAMS = {
    "response_type": "code",
    "code_challenge": "abc123challenge",
    "code_challenge_method": "S256",
    "redirect_uri": "https://claude.ai/callback",
    "client_id": "",  # overridden per test
}


@pytest.mark.asyncio
async def test_authorize_missing_google_client_id_returns_501(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oauth.settings.google_client_id", "")
    monkeypatch.setattr("oauth.settings.secret_key", "s3cr3t")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",  # type: ignore[arg-type]
    ) as client:
        resp = await client.get("/oauth/authorize", params=_AUTHORIZE_PARAMS)
    assert resp.status_code == 501


@pytest.mark.asyncio
async def test_authorize_missing_google_client_secret_returns_501(
    app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("oauth.settings.google_client_id", "gcid")
    monkeypatch.setattr("oauth.settings.google_client_secret", "")
    monkeypatch.setattr("oauth.settings.secret_key", "s3cr3t")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",  # type: ignore[arg-type]
    ) as client:
        resp = await client.get("/oauth/authorize", params=_AUTHORIZE_PARAMS)
    assert resp.status_code == 501


# ---------------------------------------------------------------------------
# create_jwt unit tests — lachesis_uid claim embedding
# ---------------------------------------------------------------------------


def test_create_jwt_includes_lachesis_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oauth.settings.secret_key", "test-secret")
    from oauth import create_jwt

    token = create_jwt("sub123", "user@example.com", lachesis_uid="db-uuid")
    claims = jose_jwt.get_unverified_claims(token)
    assert claims["lachesis_uid"] == "db-uuid"


def test_create_jwt_omits_lachesis_uid_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("oauth.settings.secret_key", "test-secret")
    from oauth import create_jwt

    token = create_jwt("sub123", "user@example.com", lachesis_uid=None)
    claims = jose_jwt.get_unverified_claims(token)
    assert "lachesis_uid" not in claims


# ---------------------------------------------------------------------------
# oauth_callback lachesis_uid integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_happy_path_stores_lachesis_uid(
    app: object, seeded_session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Supabase succeeds, lachesis_uid is stored in auth_codes."""
    from oauth_state import auth_codes

    # Clear auth_codes so we can inspect the one entry created by this test
    auth_codes.clear()

    monkeypatch.setattr("oauth.settings.mcp_base_url", "https://lachesis.example.com")
    with (
        patch("oauth._db.upsert_user", return_value={"id": "db-uuid-456"}),
        patch(
            "oauth.exchange_google_code",
            new=AsyncMock(return_value={"id": "uid123", "email": "allowed@example.com"}),
        ),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(f"/oauth/callback?code=gc&state={seeded_session}")

    assert resp.status_code == 302
    # Verify the auth code entry has the lachesis_uid from Supabase
    assert len(auth_codes) == 1
    stored_entry = next(iter(auth_codes.values()))
    assert stored_entry.get("lachesis_uid") == "db-uuid-456"


@pytest.mark.asyncio
async def test_callback_upsert_failure_is_nonfatal(
    app: object, seeded_session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-fatal: callback still redirects (302) when Supabase upsert raises."""
    monkeypatch.setattr("oauth.settings.mcp_base_url", "https://lachesis.example.com")
    with (
        patch("oauth._db.upsert_user", side_effect=RuntimeError("Supabase down")),
        patch(
            "oauth.exchange_google_code",
            new=AsyncMock(return_value={"id": "uid123", "email": "allowed@example.com"}),
        ),
    ):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
            follow_redirects=False,
        ) as client:
            resp = await client.get(f"/oauth/callback?code=gc&state={seeded_session}")

    # Must still redirect even when Supabase is down
    assert resp.status_code == 302
