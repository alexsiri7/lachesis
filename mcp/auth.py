"""Bearer token auth for the Lachesis MCP server.

Single-user: token must be a valid HS256 JWT whose 'email' claim matches the
ALLOWED_EMAIL env var. The 'sub' claim is returned as the user_id and stored
in a ContextVar populated by the ASGI middleware in main.py.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from jose import jwt
from jose.exceptions import JWTError

from settings import settings

logger = logging.getLogger(__name__)

current_user_id: ContextVar[str] = ContextVar("current_user_id")


def resolve_user_id_from_jwt(token: str) -> str | None:
    """Decode HS256 JWT signed with settings.secret_key.

    Returns the 'sub' claim if valid AND email matches ALLOWED_EMAIL, else None.
    """
    if not settings.secret_key:
        return None
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError as exc:
        logger.debug("MCP auth: JWT decode failed (%s: %s)", type(exc).__name__, exc)
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    email = payload.get("email", "")
    if not settings.allowed_email or email != settings.allowed_email:
        return None
    return str(sub)
