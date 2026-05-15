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


def resolve_user_id_from_jwt(token: str) -> tuple[str, str, str | None] | None:
    """Decode HS256 JWT signed with settings.secret_key.

    Returns ``(google_sub, email, lachesis_uid | None)`` if valid AND email
    matches ALLOWED_EMAIL, else ``None``.
    """
    if not settings.secret_key:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
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
