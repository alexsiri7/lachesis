"""Supabase persistence layer for Lachesis.

Tables (all namespaced lachesis_):
  lachesis_users  — one row per authenticated Google identity
  lachesis_repos  — per-user repo registry (populated by repo-registry tools in #12)

Uses the service-role key for all operations. Lachesis is single-user; RLS can
be added later when multi-user support lands. All lachesis_repos write functions
filter by user_id (the UUID from lachesis_users.id) so the schema is user-scoped
from day one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, cast

from supabase import Client, create_client

from settings import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> Client:
    """Return the process-lifetime Supabase client (created once via lru_cache)."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not configured")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


# ---------------------------------------------------------------------------
# lachesis_users
# ---------------------------------------------------------------------------


def upsert_user(google_id: str, email: str) -> dict[str, Any]:
    """Upsert a user by google_id. Returns the lachesis_users row (including id)."""
    now = datetime.now(UTC).isoformat()
    res = (
        _client()
        .table("lachesis_users")
        .upsert(
            {"google_id": google_id, "email": email, "updated_at": now},
            on_conflict="google_id",
        )
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise RuntimeError(f"upsert_user returned no row for google_id={google_id!r}")
    return cast(dict[str, Any], rows[0])


# ---------------------------------------------------------------------------
# lachesis_repos
# ---------------------------------------------------------------------------


def register_repo(user_id: str, repo_name: str) -> dict[str, Any]:
    """Register a repo for user_id. Idempotent (upsert on conflict)."""
    now = datetime.now(UTC).isoformat()
    res = (
        _client()
        .table("lachesis_repos")
        .upsert(
            {"user_id": user_id, "repo_name": repo_name, "updated_at": now},
            on_conflict="user_id,repo_name",
        )
        .execute()
    )
    rows = res.data or []
    if not rows:
        raise RuntimeError(f"register_repo returned no row for user_id={user_id!r}")
    return cast(dict[str, Any], rows[0])


def unregister_repo(user_id: str, repo_name: str) -> None:
    """Remove a repo from the registry for user_id. No-op if not found."""
    _client().table("lachesis_repos").delete().eq("user_id", user_id).eq(
        "repo_name", repo_name
    ).execute()


def list_repos(user_id: str) -> list[dict[str, Any]]:
    """Return all repos registered by user_id, ordered by repo_name."""
    res = (
        _client()
        .table("lachesis_repos")
        .select("id,repo_name,created_at")
        .eq("user_id", user_id)
        .order("repo_name")
        .execute()
    )
    return [cast(dict[str, Any], row) for row in (res.data or [])]
