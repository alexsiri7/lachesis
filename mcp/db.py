"""SQLAlchemy persistence layer for Lachesis.

Tables (all namespaced lachesis_):
  lachesis_users          — one row per authenticated Google identity
  lachesis_repos          — per-user repo registry, with the landing_signal that
                            decides when its requirements count as in production
  lachesis_oauth_clients  — persisted OAuth client registrations
  lachesis_refresh_tokens — persisted refresh tokens (with expiry)
  lachesis_backlog        — per-repo backlog overrides (position, ready, interrupt)
  lachesis_ntfy_config    — per-account ntfy server, topic and token
  lachesis_notifications  — one row per requirement already notified (dedupe)

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

# Connection budget on the shared database: at most POOL_SIZE + MAX_OVERFLOW.
POOL_SIZE = 3
MAX_OVERFLOW = 2


@lru_cache(maxsize=1)
def _engine() -> Engine:
    """Return the process-lifetime SQLAlchemy engine (created once via lru_cache)."""
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not configured")
    # The database is a shared Supabase Postgres (~40 usable connections across
    # every app on it), so keep this process's share small. No schema or
    # search_path is set here: the login role's search_path (lachesis, extensions)
    # resolves the unqualified table names below.
    return create_engine(
        settings.sqlalchemy_database_url,
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
    )


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


def register_repo(
    user_id: str, repo_name: str, landing_signal: str | None = None
) -> dict[str, Any]:
    """Register a repo for user_id. Idempotent (upsert on conflict).

    A *landing_signal* passed as ``None`` keeps the repo's current one, so a bare
    re-registration never resets a configured ``deployment`` back to the default.
    The COALESCE is what supplies it: the column is NOT NULL and a bound NULL
    would violate that, since server defaults apply only to omitted columns.
    """
    sql = text(
        """
        INSERT INTO lachesis_repos (user_id, repo_name, landing_signal)
        VALUES (:user_id, :repo_name, COALESCE(:landing_signal, 'merge'))
        ON CONFLICT (user_id, repo_name) DO UPDATE
            SET landing_signal = COALESCE(
                    :landing_signal, lachesis_repos.landing_signal
                ),
                updated_at = now()
        RETURNING *
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "landing_signal": landing_signal}
    with _engine().begin() as conn:
        result = conn.execute(sql, params)
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
        SELECT id, repo_name, landing_signal, created_at
        FROM lachesis_repos
        WHERE user_id = :user_id
        ORDER BY repo_name
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"user_id": user_id})
        return [dict(row) for row in result.mappings().all()]


def get_landing_signal(user_id: str, repo_name: str) -> str:
    """Return the repo's landing signal, or ``'merge'`` when it is not registered."""
    sql = text(
        """
        SELECT landing_signal
        FROM lachesis_repos
        WHERE user_id = :user_id AND repo_name = :repo_name
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"user_id": user_id, "repo_name": repo_name})
        row = result.mappings().first()
    return str(row["landing_signal"]) if row else "merge"


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


# ---------------------------------------------------------------------------
# lachesis_backlog
# ---------------------------------------------------------------------------


def list_backlog(user_id: str, repo_name: str) -> list[dict[str, Any]]:
    """Return the backlog override rows for a repo, positioned rows first."""
    sql = text(
        """
        SELECT issue_number, position, ready, interrupt
        FROM lachesis_backlog
        WHERE user_id = :user_id AND repo_name = :repo_name
        ORDER BY position ASC NULLS LAST, issue_number ASC
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"user_id": user_id, "repo_name": repo_name})
        return [dict(row) for row in result.mappings().all()]


def upsert_backlog_flags(
    user_id: str,
    repo_name: str,
    issue_number: int,
    ready: bool | None = None,
    interrupt: bool | None = None,
) -> dict[str, Any]:
    """Set the ready and/or interrupt flags for an issue. Leaves position untouched.

    A flag passed as ``None`` keeps its current value; when the row is created here
    it falls to the column default — ready true, interrupt false — so a row written
    to set ``interrupt`` alone does not park the issue as a side effect. The
    COALESCE is what supplies it: the columns are NOT NULL and a bound NULL would
    violate that, since server defaults apply only to omitted columns.
    """
    sql = text(
        """
        INSERT INTO lachesis_backlog (user_id, repo_name, issue_number, ready, interrupt)
        VALUES (
            :user_id, :repo_name, :issue_number,
            COALESCE(:ready, true), COALESCE(:interrupt, false)
        )
        ON CONFLICT (user_id, repo_name, issue_number) DO UPDATE
            SET ready = COALESCE(:ready, lachesis_backlog.ready),
                interrupt = COALESCE(:interrupt, lachesis_backlog.interrupt),
                updated_at = now()
        RETURNING *
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "issue_number": issue_number,
        "ready": ready,
        "interrupt": interrupt,
    }
    with _engine().begin() as conn:
        result = conn.execute(sql, params)
        row = result.mappings().first()
    if row is None:
        raise RuntimeError(
            f"upsert_backlog_flags returned no row for {repo_name!r} issue {issue_number}"
        )
    return dict(row)


def set_backlog_order(user_id: str, repo_name: str, ordered_issue_numbers: list[int]) -> None:
    """Persist *ordered_issue_numbers* as contiguous positions, clearing every other row.

    Runs as one transaction so the backlog is never observed half-renumbered.
    """
    clear_sql = text(
        """
        UPDATE lachesis_backlog
        SET position = NULL, updated_at = now()
        WHERE user_id = :user_id
          AND repo_name = :repo_name
          AND position IS NOT NULL
          AND NOT (issue_number = ANY(CAST(:numbers AS integer[])))
        """
    )
    place_sql = text(
        """
        INSERT INTO lachesis_backlog (user_id, repo_name, issue_number, position)
        VALUES (:user_id, :repo_name, :issue_number, :position)
        ON CONFLICT (user_id, repo_name, issue_number) DO UPDATE
            SET position = EXCLUDED.position,
                updated_at = now()
        """
    )
    with _engine().begin() as conn:
        conn.execute(
            clear_sql,
            {"user_id": user_id, "repo_name": repo_name, "numbers": ordered_issue_numbers},
        )
        for index, issue_number in enumerate(ordered_issue_numbers):
            conn.execute(
                place_sql,
                {
                    "user_id": user_id,
                    "repo_name": repo_name,
                    "issue_number": issue_number,
                    "position": index,
                },
            )


# ---------------------------------------------------------------------------
# lachesis_ntfy_config
# ---------------------------------------------------------------------------


def get_ntfy_config(user_id: str) -> dict[str, Any] | None:
    """Return the ntfy config row for user_id, or None if never configured.

    ``created_at`` is the row's creation time and stays put across
    reconfiguration, which makes it the epoch from which landings are worth
    announcing: nothing that happened before the account asked for
    notifications is news.
    """
    sql = text(
        """
        SELECT server, topic, token, created_at
        FROM lachesis_ntfy_config
        WHERE user_id = :user_id
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"user_id": user_id})
        row = result.mappings().first()
    if row is None:
        return None
    return dict(row)


def set_ntfy_config(user_id: str, server: str, topic: str, token: str) -> dict[str, Any]:
    """Replace the ntfy config for user_id. An empty topic leaves the feature inert."""
    sql = text(
        """
        INSERT INTO lachesis_ntfy_config (user_id, server, topic, token)
        VALUES (:user_id, :server, :topic, :token)
        ON CONFLICT (user_id) DO UPDATE
            SET server = EXCLUDED.server,
                topic = EXCLUDED.topic,
                token = EXCLUDED.token,
                updated_at = now()
        RETURNING server, topic, token
        """
    )
    params = {"user_id": user_id, "server": server, "topic": topic, "token": token}
    with _engine().begin() as conn:
        result = conn.execute(sql, params)
        row = result.mappings().first()
    if row is None:
        raise RuntimeError(f"set_ntfy_config returned no row for user_id={user_id!r}")
    return dict(row)


# ---------------------------------------------------------------------------
# lachesis_notifications
# ---------------------------------------------------------------------------


def notified_requirement_ids(user_id: str, repo_name: str) -> set[str]:
    """Return the ids of the repo's requirements this account was already notified about."""
    sql = text(
        """
        SELECT requirement_id
        FROM lachesis_notifications
        WHERE user_id = :user_id AND repo_name = :repo_name
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"user_id": user_id, "repo_name": repo_name})
        return {str(row["requirement_id"]) for row in result.mappings().all()}


def claim_notification(user_id: str, repo_name: str, requirement_id: str) -> bool:
    """Claim the single notification owed for a requirement, returning whether it is ours.

    True means this call won the claim and should deliver; False means the
    requirement was already claimed and must not be notified again. The absent
    row is the success signal for "already notified", not an error, so unlike the
    other writers here an empty RETURNING never raises.
    """
    sql = text(
        """
        INSERT INTO lachesis_notifications (user_id, repo_name, requirement_id)
        VALUES (:user_id, :repo_name, :requirement_id)
        ON CONFLICT (user_id, repo_name, requirement_id) DO NOTHING
        RETURNING id
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "requirement_id": requirement_id}
    with _engine().begin() as conn:
        result = conn.execute(sql, params)
        return result.mappings().first() is not None
