"""Bearer token auth for the Lachesis MCP server.

Single-user: token must be a valid HS256 JWT whose 'sub' matches the
ALLOWED_EMAIL env var. The resolved user_id is stored in a ContextVar
populated by the ASGI middleware in main.py.
"""

from __future__ import annotations

from contextvars import ContextVar

from jose import jwt
from jose.exceptions import JWTError

from settings import settings

current_user_id: ContextVar[str] = ContextVar("current_user_id")
# default=None because lachesis_uid is conditionally set — consumers must handle None
current_db_user_id: ContextVar[str | None] = ContextVar("current_db_user_id", default=None)


def _decode_jwt(token: str, verify_exp: bool = True) -> tuple[str, str, str | None] | None:
    if not settings.secret_key:
        return None
    try:
        options = {} if verify_exp else {"verify_exp": False}
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"], options=options)
    except JWTError:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    email = payload.get("email", "")
    if settings.allowed_email and email != settings.allowed_email:
        return None
    lachesis_uid = payload.get("lachesis_uid")
    return str(sub), email, (str(lachesis_uid) if lachesis_uid else None)


def resolve_user_id_from_jwt(token: str) -> tuple[str, str, str | None] | None:
    """Decode HS256 JWT signed with settings.secret_key.

    Returns ``(google_sub, email, lachesis_uid | None)`` if valid AND email
    matches ALLOWED_EMAIL, else ``None``.
    """
    return _decode_jwt(token)


def decode_jwt_ignoring_expiry(token: str) -> tuple[str, str, str | None] | None:
    """Like resolve_user_id_from_jwt but skips expiry validation.

    Used to extract user identity from an expired access token so the middleware
    can attempt a proactive refresh using a stored Lachesis refresh token.
    Returns (google_sub, email, lachesis_uid) or None if otherwise invalid.
    """
    return _decode_jwt(token, verify_exp=False)
