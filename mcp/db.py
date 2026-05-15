"""SQLAlchemy persistence layer for Lachesis.

Tables (all namespaced lachesis_):
  lachesis_users  — one row per authenticated Google identity
  lachesis_repos  — per-user repo registry (populated by repo-registry tools in #12)

Uses a direct Postgres connection via SQLAlchemy Core + psycopg2-binary.
The Postgres instance is still Supabase — only the client changes.
The env var DATABASE_URL holds a standard postgres:// connection string.

All write operations use INSERT ... ON CONFLICT DO UPDATE with RETURNING * so
callers always get the current row back.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, text

from settings import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _engine() -> Engine:
    """Return the process-lifetime SQLAlchemy engine (created once via lru_cache)."""
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not configured")
    return create_engine(settings.database_url, pool_pre_ping=True)


# ---------------------------------------------------------------------------
# lachesis_users
# ---------------------------------------------------------------------------


def upsert_user(google_id: str, email: str) -> dict[str, Any]:
    """Upsert a user by google_id. Returns the lachesis_users row (including id)."""
    sql = text(
        """
        INSERT INTO lachesis_users (google_id, email)
        VALUES (:google_id, :email)
        ON CONFLICT (google_id) DO UPDATE
            SET email = EXCLUDED.email,
                updated_at = now()
        RETURNING *
        """
    )
    with _engine().begin() as conn:
        result = conn.execute(sql, {"google_id": google_id, "email": email})
        row = result.mappings().first()
    if row is None:
        raise RuntimeError(f"upsert_user returned no row for google_id={google_id!r}")
    return dict(row)


# ---------------------------------------------------------------------------
# lachesis_repos
# ---------------------------------------------------------------------------


def register_repo(user_id: str, repo_name: str) -> dict[str, Any]:
    """Register a repo for user_id. Idempotent (upsert on conflict)."""
    sql = text(
        """
        INSERT INTO lachesis_repos (user_id, repo_name)
        VALUES (:user_id, :repo_name)
        ON CONFLICT (user_id, repo_name) DO UPDATE
            SET updated_at = now()
        RETURNING *
        """
    )
    with _engine().begin() as conn:
        result = conn.execute(sql, {"user_id": user_id, "repo_name": repo_name})
        row = result.mappings().first()
    if row is None:
        raise RuntimeError(f"register_repo returned no row for user_id={user_id!r}")
    return dict(row)


def unregister_repo(user_id: str, repo_name: str) -> None:
    """Remove a repo from the registry for user_id. No-op if not found."""
    sql = text(
        """
        DELETE FROM lachesis_repos
        WHERE user_id = :user_id AND repo_name = :repo_name
        """
    )
    with _engine().begin() as conn:
        conn.execute(sql, {"user_id": user_id, "repo_name": repo_name})


def list_repos(user_id: str) -> list[dict[str, Any]]:
    """Return all repos registered by user_id, ordered by repo_name."""
    sql = text(
        """
        SELECT id, repo_name, created_at
        FROM lachesis_repos
        WHERE user_id = :user_id
        ORDER BY repo_name
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"user_id": user_id})
        return [dict(row) for row in result.mappings().all()]
