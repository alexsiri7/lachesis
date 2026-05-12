"""Tests for auth.py — JWT validation and ALLOWED_EMAIL enforcement."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from jose import jwt

import auth
from auth import resolve_user_id_from_jwt

SECRET = "test-secret-key"
ALLOWED = "user@example.com"


def make_token(
    sub: str = "uid-1",
    email: str = ALLOWED,
    secret: str = SECRET,
    algorithm: str = "HS256",
) -> str:
    return str(jwt.encode({"sub": sub, "email": email}, secret, algorithm=algorithm))


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch.object(auth, "settings") as s:
        s.secret_key = SECRET
        s.allowed_email = ALLOWED
        yield s


def test_valid_token_returns_sub(fake_settings: object) -> None:
    assert resolve_user_id_from_jwt(make_token()) == "uid-1"


def test_wrong_email_returns_none(fake_settings: object) -> None:
    assert resolve_user_id_from_jwt(make_token(email="evil@example.com")) is None


def test_invalid_signature_returns_none(fake_settings: object) -> None:
    assert resolve_user_id_from_jwt(make_token(secret="wrong-secret")) is None


def test_missing_sub_returns_none(fake_settings: object) -> None:
    token = str(jwt.encode({"email": ALLOWED}, SECRET, algorithm="HS256"))
    assert resolve_user_id_from_jwt(token) is None


def test_no_secret_key_returns_none(fake_settings: object) -> None:
    fake_settings.secret_key = ""  # type: ignore[union-attr]
    assert resolve_user_id_from_jwt(make_token()) is None


def test_empty_allowed_email_blocks_all(fake_settings: object) -> None:
    # After fixing the inverted guard, empty ALLOWED_EMAIL must deny everyone
    fake_settings.allowed_email = ""  # type: ignore[union-attr]
    assert resolve_user_id_from_jwt(make_token()) is None


def test_empty_allowed_email_blocks_correct_email(fake_settings: object) -> None:
    # Even the "correct" email is denied when ALLOWED_EMAIL is unset
    fake_settings.allowed_email = ""  # type: ignore[union-attr]
    assert resolve_user_id_from_jwt(make_token(email=ALLOWED)) is None
