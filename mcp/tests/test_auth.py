"""Tests for mcp/auth.py — resolve_user_id_from_jwt return type and lachesis_uid extraction."""

from __future__ import annotations

import pytest
from jose import jwt as jose_jwt

from auth import resolve_user_id_from_jwt

SECRET = "test-secret"


def _make_jwt(
    sub: str,
    email: str,
    lachesis_uid: str | None = None,
    secret: str = SECRET,
) -> str:
    """Mint a minimal HS256 JWT for testing."""
    payload: dict[str, object] = {"sub": sub, "email": email}
    if lachesis_uid is not None:
        payload["lachesis_uid"] = lachesis_uid
    return str(jose_jwt.encode(payload, secret, algorithm="HS256"))


def test_resolve_returns_tuple_with_lachesis_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("auth.settings.secret_key", SECRET)
    monkeypatch.setattr("auth.settings.allowed_email", "user@example.com")
    token = _make_jwt("google-sub", "user@example.com", lachesis_uid="db-uuid-123")
    result = resolve_user_id_from_jwt(token)
    assert result == ("google-sub", "db-uuid-123")


def test_resolve_returns_none_lachesis_uid_when_claim_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("auth.settings.secret_key", SECRET)
    monkeypatch.setattr("auth.settings.allowed_email", "user@example.com")
    token = _make_jwt("google-sub", "user@example.com")
    result = resolve_user_id_from_jwt(token)
    assert result == ("google-sub", None)


def test_resolve_returns_none_for_wrong_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("auth.settings.secret_key", SECRET)
    monkeypatch.setattr("auth.settings.allowed_email", "user@example.com")
    token = _make_jwt("google-sub", "attacker@evil.com")
    assert resolve_user_id_from_jwt(token) is None


def test_resolve_returns_none_for_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("auth.settings.secret_key", SECRET)
    monkeypatch.setattr("auth.settings.allowed_email", "user@example.com")
    assert resolve_user_id_from_jwt("not.a.jwt") is None


def test_resolve_returns_none_when_no_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("auth.settings.secret_key", "")
    monkeypatch.setattr("auth.settings.allowed_email", "user@example.com")
    token = _make_jwt("google-sub", "user@example.com")
    assert resolve_user_id_from_jwt(token) is None


def test_resolve_tuple_shape_is_str_str_or_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the tuple return type contract: (str, str | None)."""
    monkeypatch.setattr("auth.settings.secret_key", SECRET)
    monkeypatch.setattr("auth.settings.allowed_email", "user@example.com")
    token_with = _make_jwt("google-sub", "user@example.com", lachesis_uid="uid-abc")
    token_without = _make_jwt("google-sub", "user@example.com")

    result_with = resolve_user_id_from_jwt(token_with)
    result_without = resolve_user_id_from_jwt(token_without)

    assert result_with is not None
    user_id_with, lachesis_uid_with = result_with
    assert isinstance(user_id_with, str)
    assert isinstance(lachesis_uid_with, str)

    assert result_without is not None
    user_id_without, lachesis_uid_without = result_without
    assert isinstance(user_id_without, str)
    assert lachesis_uid_without is None
