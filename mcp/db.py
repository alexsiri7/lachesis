"""SQLAlchemy persistence layer for Lachesis.

Tables (all namespaced lachesis_):
  lachesis_users          — one row per authenticated Google identity
  lachesis_repos          — per-user repo registry (populated by repo-registry tools in #12)
  lachesis_oauth_clients  — persisted OAuth client registrations
  lachesis_refresh_tokens — persisted refresh tokens (with expiry)

Uses a direct Postgres connection via SQLAlchemy Core + psycopg2-binary.
The Postgres instance is still Supabase — only the client changes.
The env var DATABASE_URL holds a standard postgres:// connection string.

All write operations use INSERT ... ON CONFLICT DO UPDATE with RETURNING * so
callers always get the current row back.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
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
# Health probe
# ---------------------------------------------------------------------------


def ping() -> None:
    """Raise if the DB is unreachable or lachesis_users is missing."""
    with _engine().connect() as conn:
        conn.execute(text("SELECT 1 FROM lachesis_users LIMIT 1"))


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


# ---------------------------------------------------------------------------
# lachesis_oauth_clients
# ---------------------------------------------------------------------------


def upsert_oauth_client(client_id: str, data: dict[str, Any]) -> None:
    """Persist an OAuth client registration. Idempotent."""
    sql = text(
        """
        INSERT INTO lachesis_oauth_clients (client_id, data)
        VALUES (:client_id, CAST(:data AS jsonb))
        ON CONFLICT (client_id) DO UPDATE
            SET data = EXCLUDED.data
        """
    )
    with _engine().begin() as conn:
        conn.execute(sql, {"client_id": client_id, "data": json.dumps(data, default=str)})


def get_oauth_client(client_id: str) -> dict[str, Any] | None:
    """Return the client registration dict for client_id, or None if not found."""
    sql = text(
        """
        SELECT data FROM lachesis_oauth_clients WHERE client_id = :client_id
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"client_id": client_id})
        row = result.mappings().first()
    if row is None:
        return None
    return dict(row["data"])


# ---------------------------------------------------------------------------
# lachesis_refresh_tokens
# ---------------------------------------------------------------------------


def upsert_refresh_token(token: str, data: dict[str, Any], expires_at: datetime) -> None:
    """Persist a refresh token with its expiry. Idempotent."""
    sql = text(
        """
        INSERT INTO lachesis_refresh_tokens (token, data, expires_at)
        VALUES (:token, CAST(:data AS jsonb), :expires_at)
        ON CONFLICT (token) DO UPDATE
            SET data = EXCLUDED.data,
                expires_at = EXCLUDED.expires_at
        """
    )
    with _engine().begin() as conn:
        conn.execute(
            sql,
            {
                "token": token,
                "data": json.dumps(data, default=str),
                "expires_at": expires_at,
            },
        )


def get_refresh_token(token: str) -> dict[str, Any] | None:
    """Return the refresh token data dict, or None if not found or expired."""
    sql = text(
        """
        SELECT data FROM lachesis_refresh_tokens
        WHERE token = :token AND expires_at > now()
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"token": token})
        row = result.mappings().first()
    if row is None:
        return None
    return dict(row["data"])


def delete_refresh_token(token: str) -> None:
    """Delete a refresh token. No-op if not found."""
    sql = text("DELETE FROM lachesis_refresh_tokens WHERE token = :token")
    with _engine().begin() as conn:
        conn.execute(sql, {"token": token})


def get_refresh_token_by_user_id(user_id: str) -> tuple[str, dict[str, Any]] | None:
    """Return the most recent valid refresh token for a given Google sub ID, or None."""
    sql = text(
        """
        SELECT token, data FROM lachesis_refresh_tokens
        WHERE data->>'user_id' = :user_id AND expires_at > now()
        ORDER BY created_at DESC LIMIT 1
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"user_id": user_id})
        row = result.mappings().first()
    if row is None:
        return None
    return str(row["token"]), dict(row["data"])


def list_active_refresh_tokens() -> list[dict[str, Any]]:
    """Return all non-expired refresh tokens, ordered by expiry (soonest first)."""
    sql = text(
        """
        SELECT token, data, expires_at FROM lachesis_refresh_tokens
        WHERE expires_at > now()
        ORDER BY expires_at ASC
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql)
        rows = result.mappings().all()
    return [
        {"token": str(r["token"]), "data": dict(r["data"]), "expires_at": r["expires_at"]}
        for r in rows
    ]
