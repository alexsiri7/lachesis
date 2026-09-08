"""Lachesis MCP tool implementations — ntfy notification configuration."""

from __future__ import annotations

import logging
from typing import Any

import db
import notify

from .repos import _require_user_id

logger = logging.getLogger(__name__)


def _public_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Describe a config without disclosing the token."""
    if config is None:
        return {"server": notify.DEFAULT_SERVER, "topic": "", "token_set": False}
    return {
        "server": config["server"],
        "topic": config["topic"],
        "token_set": bool(config["token"]),
    }


async def set_ntfy_config(
    topic: str,
    server: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Set the account's ntfy configuration. An empty topic clears it.

    An omitted server or token keeps whatever is already stored, so renaming a
    topic does not silently drop the auth token that protects it.
    """
    user_id = _require_user_id()
    existing = db.get_ntfy_config(user_id) if server is None or token is None else None
    if server is None:
        server = existing["server"] if existing else notify.DEFAULT_SERVER
    if token is None:
        token = existing["token"] if existing else ""
    config = db.set_ntfy_config(user_id, server.strip(), topic.strip(), token.strip())
    return _public_config(config)


async def send_test_notification() -> dict[str, Any]:
    """Send a test notification to the configured topic, bypassing the dedupe record."""
    user_id = _require_user_id()
    config = db.get_ntfy_config(user_id)
    if config is None or not config["topic"]:
        return {
            "sent": False,
            "reason": "no topic configured — notifications are inert until one is set",
        }

    delivered = await notify.send(
        user_id,
        title="Lachesis test notification",
        body="Notifications are configured correctly.",
        tags=["white_check_mark"],
    )
    result = _public_config(config)
    result["sent"] = delivered
    if not delivered:
        result["reason"] = "delivery failed — see server logs"
    return result
