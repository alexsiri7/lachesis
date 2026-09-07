"""Best-effort ntfy delivery for account-configured push notifications.

Nothing in Lachesis depends on a notification having been sent: every entry
point here logs and drops on failure rather than raising into its caller, so a
missing config or an unreachable ntfy server never blocks a status transition.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from email.header import Header

import httpx

import db
from tools.repos import normalize_repo

logger = logging.getLogger(__name__)

DEFAULT_SERVER = "https://ntfy.sh"


def _header_value(value: str) -> str:
    """Encode *value* for an HTTP header, as RFC 2047 when it is not ASCII.

    ntfy carries the title and tags as headers, which are latin-1 at best — an
    em dash in a requirement title would otherwise fail inside httpx before the
    request leaves the process. ``maxlinelen`` defeats the header folding that
    would otherwise split a long title across lines: a newline mid-header is no
    more deliverable than the em dash was.
    """
    if value.isascii():
        return value
    return Header(value, "utf-8").encode(maxlinelen=998)


async def send(
    user_id: str,
    *,
    title: str,
    body: str,
    click: str | None = None,
    tags: Sequence[str] = (),
) -> bool:
    """POST a notification to the account's ntfy topic. Returns whether it was delivered.

    Never raises. Returns False when no topic is configured — the inert state —
    which is indistinguishable to the caller from a delivery that failed,
    because no caller is allowed to care.
    """
    try:
        config = db.get_ntfy_config(user_id)
    except Exception:
        logger.warning("notify: could not read ntfy config for user=%s", user_id, exc_info=True)
        return False

    if config is None or not config["topic"]:
        return False

    server = (config["server"] or DEFAULT_SERVER).rstrip("/")
    topic = config["topic"]

    headers = {"Title": _header_value(title)}
    if click:
        headers["Click"] = click
    if tags:
        headers["Tags"] = _header_value(",".join(tags))
    if config["token"]:
        headers["Authorization"] = f"Bearer {config['token']}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                f"{server}/{topic}", headers=headers, content=body.encode("utf-8")
            )
        resp.raise_for_status()
    except Exception:
        logger.warning("notify: delivery to %s/%s failed", server, topic, exc_info=True)
        return False

    logger.info("notify: delivered to %s/%s", server, topic)
    return True


async def notify_requirement(
    user_id: str,
    *,
    repo: str,
    requirement_id: str,
    title: str,
    body: str,
    click: str | None = None,
    tags: Sequence[str] = (),
) -> bool:
    """Notify once for a requirement. Returns whether this call delivered it.

    The claim is taken before delivery is attempted, so a requirement whose
    notification could not be delivered is dropped rather than retried on the
    next evaluation. That is what lets the landing trigger run repeatedly
    without the author receiving the same notification twice.
    """
    try:
        repo_name = normalize_repo(repo)
    except ValueError:
        logger.warning("notify: cannot notify for unparseable repo %r", repo)
        return False

    try:
        claimed = db.claim_notification(user_id, repo_name, requirement_id)
    except Exception:
        logger.warning(
            "notify: could not claim notification for %s %s",
            repo_name,
            requirement_id,
            exc_info=True,
        )
        return False

    if not claimed:
        logger.info("notify: %s %s already notified, suppressed", repo_name, requirement_id)
        return False

    return await send(user_id, title=title, body=body, click=click, tags=tags)
