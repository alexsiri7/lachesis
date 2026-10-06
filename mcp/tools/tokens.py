"""Lachesis MCP tool implementations — factory tokens: keys unattended scripts act with."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

import db
from auth import via_factory_token

from .repos import _require_user_id

TOKEN_PREFIX = "lft_"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _require_signed_in() -> str:
    if via_factory_token.get():
        raise PermissionError(
            "factory tokens can only be created or revoked from a Google sign-in, "
            "not with a factory token"
        )
    return _require_user_id()


async def create_factory_token(name: str) -> dict[str, Any]:
    """Create the factory token *name* and return it once; only its hash is kept."""
    name = name.strip()
    if not name:
        raise ValueError("name must not be empty")
    user_id = _require_signed_in()
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    created = db.create_factory_token(user_id, name, token_hash(token))
    return {**created, "token": token, "note": "Shown once: store it now."}


async def list_factory_tokens() -> dict[str, Any]:
    """Return the active factory tokens by name, creation and last use, never their values."""
    return {"tokens": db.list_factory_tokens(_require_user_id())}


async def revoke_factory_token(name: str) -> dict[str, Any]:
    """Revoke the factory token *name*; it is refused from then on."""
    user_id = _require_signed_in()
    if not db.revoke_factory_token(user_id, name.strip()):
        raise ValueError(f"no active factory token is named {name!r}")
    return {"name": name.strip(), "revoked": True}
