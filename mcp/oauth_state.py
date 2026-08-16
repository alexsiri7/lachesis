"""Shared in-memory state for the MCP OAuth 2.1 flow.

Two bounded dicts back the short-lived endpoints in ``oauth.py``. Expired
entries are lazily purged on every store/retrieve, and each dict is hard-capped
at ``MAX_ENTRIES_PER_DICT`` to avoid unbounded growth.

``registered_clients`` and ``refresh_tokens`` are persisted to Postgres (see
``db.py``) so they survive Railway redeploys.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

MAX_ENTRIES_PER_DICT = 10_000

# Lock protecting all mutable state dicts below against concurrent access
# from threadpool-dispatched sync endpoints.
_state_lock = threading.Lock()

Entry = dict[str, Any]

# server_state -> {
#   client_state, redirect_uri, code_challenge, code_challenge_method,
#   client_id, scope, expires_at
# }
oauth_sessions: dict[str, Entry] = {}

# auth_code -> {
#   user_id, email, code_challenge, code_challenge_method, redirect_uri, expires_at
# }
auth_codes: dict[str, Entry] = {}


def _is_expired(entry: Entry, now_ts: float, now_dt: datetime) -> bool:
    exp = entry.get("expires_at")
    if exp is None:
        return False
    if isinstance(exp, datetime):
        return now_dt > exp
    # numeric (epoch seconds)
    return now_ts > exp


def _cleanup_expired(store: dict[str, Entry]) -> None:
    """Remove entries whose ``expires_at`` is in the past.

    Entries may store ``expires_at`` as either a timezone-aware ``datetime``
    or a ``float`` (Unix epoch). Entries without ``expires_at`` (e.g.
    registered clients) are never evicted by this function.
    """
    now_ts = time.time()
    now_dt = datetime.now(UTC)
    expired_keys = [k for k, v in store.items() if _is_expired(v, now_ts, now_dt)]
    for k in expired_keys:
        del store[k]
    if expired_keys:
        logger.debug("oauth_state: purged %d expired entries", len(expired_keys))


class StoreFullError(Exception):
    """Raised when a bounded dict exceeds MAX_ENTRIES_PER_DICT after cleanup."""


def cleanup_and_store(store: dict[str, Entry], key: str, value: Entry) -> None:
    """Purge expired entries, enforce size cap, then insert *key*: *value*."""
    with _state_lock:
        _cleanup_expired(store)
        if len(store) >= MAX_ENTRIES_PER_DICT:
            raise StoreFullError(f"OAuth state store is full ({MAX_ENTRIES_PER_DICT} entries)")
        store[key] = value


def cleanup_and_get(store: dict[str, Entry], key: str) -> Entry | None:
    """Purge expired entries, then return the entry for *key* (or ``None``)."""
    with _state_lock:
        _cleanup_expired(store)
        return store.get(key)


def cleanup_and_pop(store: dict[str, Entry], key: str) -> Entry | None:
    """Purge expired entries, then pop and return the entry for *key* (or ``None``)."""
    with _state_lock:
        _cleanup_expired(store)
        return store.pop(key, None)
