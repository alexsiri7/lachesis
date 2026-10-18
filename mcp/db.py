"""SQLAlchemy persistence layer for Lachesis.

Tables (all namespaced lachesis_):
  lachesis_users          — one row per authenticated Google identity
  lachesis_repos          — per-user repo registry, with the landing_signal that
                            decides when its requirements count as in production
  lachesis_oauth_clients  — persisted OAuth client registrations
  lachesis_refresh_tokens — persisted refresh tokens (with expiry)
  lachesis_backlog        — per-repo backlog flags (ready, interrupt, when last unparked)
  lachesis_focus          — the repositories and milestones the author has focused on
  lachesis_ntfy_config    — per-account ntfy server, topic and token
  lachesis_notifications  — one row per requirement already notified (dedupe)
  lachesis_events         — append-only log of changes to the factory's records
  lachesis_questions      — questions for the author and the issues each one blocks
  lachesis_milestones     — milestones and when the author started or dropped them
  lachesis_milestone_spec_changes — the spec changes each milestone groups, one milestone each
  lachesis_accounts       — the Claude accounts the factory may use: share, five-hour
                            pause threshold, and which one is primary
  lachesis_fuel           — the latest allowance status reported for each account
  lachesis_overflow       — the latest Requesty overflow spend and cap reported
  lachesis_factory_tokens — named, revocable keys scripts use to act as the author
  lachesis_estimates      — the current token estimate of each issue, with its kind,
                            capability and confidence
  lachesis_run_outcomes   — failures reported against a run: rework, a retry or an
                            audit finding
  lachesis_issue_sizes    — each issue's size in Fibonacci points, 21 meaning larger
                            than 13
  lachesis_audit_runs     — every audit run: repo, kind, capability, model, when it
                            finished, outcome, issues filed and tokens
  lachesis_experiments    — improvements to how the factory works: the change, its
                            measure, baseline and target, its issues, state and results
  lachesis_automerge_refusals — why automerge could not be enabled on a Lachesis pull
                            request

Uses a direct Postgres connection via SQLAlchemy Core + psycopg2-binary.
The Postgres instance is still Supabase — only the client changes.
The env var DATABASE_URL holds a standard postgres:// connection string.

All write operations use INSERT ... ON CONFLICT DO UPDATE with RETURNING * so
callers always get the current row back.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Literal, get_args

from sqlalchemy import Connection, Engine, create_engine, text

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
    """Return the backlog flag rows for a repo, by issue number."""
    sql = text(
        """
        SELECT issue_number, ready, interrupt, ready_since
        FROM lachesis_backlog
        WHERE user_id = :user_id AND repo_name = :repo_name
        ORDER BY issue_number ASC
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
    """Set the ready and/or interrupt flags for an issue.

    A flag passed as ``None`` keeps its current value; when the row is created here
    it falls to the column default — ready true, interrupt false — so a row written
    to set ``interrupt`` alone does not park the issue as a side effect. The
    COALESCE is what supplies it: the columns are NOT NULL and a bound NULL would
    violate that, since server defaults apply only to omitted columns.

    Unparking an issue stamps ``ready_since``; setting ready on an issue already
    ready leaves it, so ageing counts from the last unpark.
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
                ready_since = CASE
                    WHEN COALESCE(:ready, false) AND NOT lachesis_backlog.ready THEN now()
                    ELSE lachesis_backlog.ready_since
                END,
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


# ---------------------------------------------------------------------------
# lachesis_focus
# ---------------------------------------------------------------------------


def add_focus(user_id: str, repo_name: str, milestone_id: int | None) -> None:
    """Focus *repo_name*, or one of its milestones; focusing it again changes nothing."""
    sql = text(
        """
        INSERT INTO lachesis_focus (user_id, repo_name, milestone_id)
        VALUES (:user_id, :repo_name, :milestone_id)
        ON CONFLICT DO NOTHING
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "milestone_id": milestone_id}
    with _engine().begin() as conn:
        conn.execute(sql, params)


def remove_focus(user_id: str, repo_name: str, milestone_id: int | None) -> None:
    """Clear exactly this focus: a repository's leaves its milestones' focuses alone."""
    sql = text(
        """
        DELETE FROM lachesis_focus
        WHERE user_id = :user_id AND repo_name = :repo_name
          AND milestone_id IS NOT DISTINCT FROM :milestone_id
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "milestone_id": milestone_id}
    with _engine().begin() as conn:
        conn.execute(sql, params)


def list_focus(user_id: str) -> list[dict[str, Any]]:
    """Return every focus of the account, ``repo_name`` and ``milestone_id``, oldest first."""
    sql = text(
        """
        SELECT repo_name, milestone_id
        FROM lachesis_focus
        WHERE user_id = :user_id
        ORDER BY created_at, id
        """
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"user_id": user_id}).mappings().all()]


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
    """Return the ids of the repo's requirements and spec changes already notified about."""
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
    """Claim the single notification owed for a unit of work, returning whether it is ours.

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


# ---------------------------------------------------------------------------
# lachesis_automerge_refusals
# ---------------------------------------------------------------------------


def record_automerge_refusal(user_id: str, repo_name: str, pr_number: int, reason: str) -> None:
    """Store why automerge could not be enabled on pull request *pr_number*."""
    sql = text(
        """
        INSERT INTO lachesis_automerge_refusals (user_id, repo_name, pr_number, reason)
        VALUES (:user_id, :repo_name, :pr_number, :reason)
        ON CONFLICT (user_id, repo_name, pr_number)
        DO UPDATE SET reason = EXCLUDED.reason, created_at = now()
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "pr_number": pr_number, "reason": reason}
    with _engine().begin() as conn:
        conn.execute(sql, params)


def automerge_refusals(user_id: str, repo_name: str) -> dict[int, str]:
    """Return the recorded automerge refusal reason of each of the repo's pull requests."""
    sql = text(
        """
        SELECT pr_number, reason
        FROM lachesis_automerge_refusals
        WHERE user_id = :user_id AND repo_name = :repo_name
        """
    )
    with _engine().connect() as conn:
        result = conn.execute(sql, {"user_id": user_id, "repo_name": repo_name})
        return {row["pr_number"]: row["reason"] for row in result.mappings().all()}


# ---------------------------------------------------------------------------
# lachesis_events
# ---------------------------------------------------------------------------

EventKind = Literal[
    "question_asked",
    "question_answered",
    "note_added",
    "milestone_created",
    "milestone_started",
    "milestone_dropped",
    "milestone_state_changed",
    "milestone_spec_change_added",
    "milestone_spec_change_removed",
    "usage_reported",
    "run_outcome_reported",
]


def record_event(user_id: str, repo_name: str, kind: EventKind, subject: str) -> dict[str, Any]:
    """Append an event naming *subject* in *repo_name*. Events are never changed afterwards."""
    if kind not in get_args(EventKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(EventKind))}, got {kind!r}")
    with _engine().begin() as conn:
        return _insert_event(conn, user_id, repo_name, kind, subject)


def _insert_event(
    conn: Connection, user_id: str, repo_name: str, kind: EventKind, subject: str
) -> dict[str, Any]:
    sql = text(
        """
        INSERT INTO lachesis_events (user_id, repo_name, kind, subject)
        VALUES (:user_id, :repo_name, :kind, :subject)
        RETURNING id, repo_name, kind, subject, created_at
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "kind": kind, "subject": subject}
    row = conn.execute(sql, params).mappings().first()
    if row is None:
        raise RuntimeError(f"record_event returned no row for {repo_name!r} {kind}")
    return dict(row)


def list_events(
    user_id: str,
    after: int,
    limit: int,
    repo_name: str | None = None,
    kind: EventKind | None = None,
) -> list[dict[str, Any]]:
    """Return up to *limit* events with an id above *after*, oldest first.

    A filter passed as ``None`` matches every value.
    """
    sql = text(
        """
        SELECT id, repo_name, kind, subject, created_at
        FROM lachesis_events
        WHERE user_id = :user_id
          AND id > :after
          AND (CAST(:repo_name AS text) IS NULL OR repo_name = :repo_name)
          AND (CAST(:kind AS text) IS NULL OR kind = :kind)
        ORDER BY id
        LIMIT :limit
        """
    )
    params = {
        "user_id": user_id,
        "after": after,
        "limit": limit,
        "repo_name": repo_name,
        "kind": kind,
    }
    with _engine().connect() as conn:
        result = conn.execute(sql, params)
        return [dict(row) for row in result.mappings().all()]


# ---------------------------------------------------------------------------
# lachesis_questions
# ---------------------------------------------------------------------------

QuestionKind = Literal[
    "spec_contradiction",
    "large_work",
    "maturity",
    "improvement",
    "budget",
    "clarification",
]


def record_question(
    user_id: str,
    repo_name: str,
    question: str,
    kind: QuestionKind,
    asker: str,
    *,
    context: str = "",
    options: list[str] | None = None,
    capability: str | None = None,
    spec_change: int | None = None,
    milestone: str | None = None,
    issue: int | None = None,
    blocks: list[int] | None = None,
    default_answer: str | None = None,
    wait: timedelta | None = None,
) -> dict[str, Any]:
    """Store a question and its ``question_asked`` event in one transaction.

    A *default_answer* is taken once *wait* has passed from now with no answer.
    """
    if kind not in get_args(QuestionKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(QuestionKind))}, got {kind!r}")
    sql = text(
        """
        INSERT INTO lachesis_questions (
            user_id, repo_name, question, context, options, kind, asker,
            capability, spec_change, milestone, issue, blocks,
            default_answer, default_after
        )
        VALUES (
            :user_id, :repo_name, :question, :context, :options, :kind, :asker,
            :capability, :spec_change, :milestone, :issue, :blocks,
            :default_answer, now() + :wait
        )
        RETURNING id, repo_name, question, context, options, kind, asker,
                  capability, spec_change, milestone, issue, blocks, created_at,
                  default_answer, default_after
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "question": question,
        "context": context,
        "options": options or [],
        "kind": kind,
        "asker": asker,
        "capability": capability,
        "spec_change": spec_change,
        "milestone": milestone,
        "issue": issue,
        "blocks": blocks or [],
        "default_answer": default_answer,
        "wait": wait if default_answer is not None else None,
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
        if row is None:
            raise RuntimeError(f"record_question returned no row for {repo_name!r}")
        _insert_event(conn, user_id, repo_name, "question_asked", str(row["id"]))
    return dict(row)


OutcomeKind = Literal["none", "standing_rule", "spec_change", "new_project", "defaulted"]
DEFAULTED: OutcomeKind = "defaulted"

_QUESTION_COLUMNS = """id, repo_name, question, context, options, kind, asker,
                  capability, spec_change, milestone, issue, blocks, created_at,
                  answer, answerer, answered_at, outcome, default_answer, default_after"""


def get_question(user_id: str, repo_name: str, question_id: int) -> dict[str, Any] | None:
    """Return question *question_id* in *repo_name*, or ``None`` if it is not there."""
    sql = text(
        f"""
        SELECT {_QUESTION_COLUMNS}
        FROM lachesis_questions
        WHERE user_id = :user_id AND repo_name = :repo_name AND id = :id
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "id": question_id}
    with _engine().connect() as conn:
        row = conn.execute(sql, params).mappings().first()
    return dict(row) if row is not None else None


def open_questions_blocking(user_id: str, repo_name: str, issue: int) -> list[int]:
    """Return the ids of the open questions in *repo_name* that block *issue*, oldest first."""
    sql = text(
        """
        SELECT id
        FROM lachesis_questions
        WHERE user_id = :user_id AND repo_name = :repo_name
          AND answered_at IS NULL AND :issue = ANY(blocks)
        ORDER BY id
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "issue": issue}
    with _engine().connect() as conn:
        return [row["id"] for row in conn.execute(sql, params).mappings().all()]


def list_open_questions(user_id: str) -> list[dict[str, Any]]:
    """Return the open questions in every repo *user_id* has registered, oldest first."""
    sql = text(
        f"""
        SELECT {_QUESTION_COLUMNS}
        FROM lachesis_questions
        WHERE user_id = :user_id AND answered_at IS NULL
          AND repo_name IN (SELECT repo_name FROM lachesis_repos WHERE user_id = :user_id)
        ORDER BY created_at, id
        """
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"user_id": user_id}).mappings().all()]


def list_expired_defaults(user_id: str) -> list[dict[str, Any]]:
    """Return the open questions whose wait for a default has passed, oldest first.

    Only questions in repositories *user_id* has registered are returned.
    """
    sql = text(
        f"""
        SELECT {_QUESTION_COLUMNS}
        FROM lachesis_questions
        WHERE user_id = :user_id AND answered_at IS NULL
          AND default_after IS NOT NULL AND default_after <= now()
          AND repo_name IN (SELECT repo_name FROM lachesis_repos WHERE user_id = :user_id)
        ORDER BY created_at, id
        """
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"user_id": user_id}).mappings().all()]


def list_defaulted_questions(user_id: str, since: datetime) -> list[dict[str, Any]]:
    """Return the questions settled by their default since *since*, oldest first.

    Only questions in repositories *user_id* has registered are returned.
    """
    sql = text(
        f"""
        SELECT {_QUESTION_COLUMNS}
        FROM lachesis_questions
        WHERE user_id = :user_id AND outcome = 'defaulted' AND answered_at >= :since
          AND repo_name IN (SELECT repo_name FROM lachesis_repos WHERE user_id = :user_id)
        ORDER BY answered_at, id
        """
    )
    params = {"user_id": user_id, "since": since}
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).mappings().all()]


def search_answered_questions(user_id: str, repo_name: str, query: str) -> list[dict[str, Any]]:
    """Return the answered questions in *repo_name* matching *query*, newest answer first.

    Every word of *query* must match, by English stem, somewhere in the question,
    its context or its answer.
    """
    sql = text(
        f"""
        SELECT {_QUESTION_COLUMNS}
        FROM lachesis_questions
        WHERE user_id = :user_id AND repo_name = :repo_name
          AND answered_at IS NOT NULL
          AND to_tsvector('english', question || ' ' || context || ' ' || answer)
              @@ plainto_tsquery('english', :query)
        ORDER BY answered_at DESC, id DESC
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "query": query}
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).mappings().all()]


def answer_question(
    user_id: str,
    repo_name: str,
    question_id: int,
    answer: str,
    answerer: str,
    outcome: OutcomeKind,
) -> dict[str, Any]:
    """Record the answer to an open question and its ``question_answered`` event.

    A question settled by its default (outcome ``defaulted``) may be answered again;
    the new answer replaces the default.

    Returns the answered question with ``released``: the issues it blocked that no
    other open question still blocks. Raises ``ValueError`` unless the question is
    in *repo_name* and still open or defaulted.
    """
    if outcome not in get_args(OutcomeKind):
        raise ValueError(
            f"outcome must be one of {', '.join(get_args(OutcomeKind))}, got {outcome!r}"
        )
    answer_sql = text(
        f"""
        UPDATE lachesis_questions
        SET answer = :answer, answerer = :answerer, answered_at = now(), outcome = :outcome
        WHERE user_id = :user_id AND repo_name = :repo_name AND id = :id
          AND (answered_at IS NULL OR outcome = 'defaulted')
        RETURNING {_QUESTION_COLUMNS}
        """
    )
    still_blocked_sql = text(
        """
        SELECT DISTINCT unnest(blocks) AS issue
        FROM lachesis_questions
        WHERE user_id = :user_id AND repo_name = :repo_name
          AND answered_at IS NULL AND blocks && CAST(:blocks AS integer[])
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "id": question_id,
        "answer": answer,
        "answerer": answerer,
        "outcome": outcome,
    }
    with _engine().begin() as conn:
        row = conn.execute(answer_sql, params).mappings().first()
        if row is None:
            raise ValueError(f"Question {question_id} in {repo_name!r} is not open")
        _insert_event(conn, user_id, repo_name, "question_answered", str(row["id"]))
        still_blocked = {
            r["issue"]
            for r in conn.execute(
                still_blocked_sql,
                {"user_id": user_id, "repo_name": repo_name, "blocks": row["blocks"]},
            )
            .mappings()
            .all()
        }
    return {**row, "released": [n for n in row["blocks"] if n not in still_blocked]}


# ---------------------------------------------------------------------------
# lachesis_notes
# ---------------------------------------------------------------------------

NoteType = Literal["handoff", "finding", "progress", "decision", "report"]

_NOTE_COLUMNS = "id, repo_name, issue, milestone, role, type, text, created_at"


def record_note(
    user_id: str,
    repo_name: str,
    role: str,
    note_type: NoteType,
    body: str,
    *,
    issue: int | None = None,
    milestone: str | None = None,
) -> dict[str, Any]:
    """Store a note on exactly one of *issue* or *milestone* and its ``note_added`` event."""
    if note_type not in get_args(NoteType):
        raise ValueError(f"type must be one of {', '.join(get_args(NoteType))}, got {note_type!r}")
    if (issue is None) == (milestone is None):
        raise ValueError("a note must name exactly one issue or milestone")
    sql = text(
        f"""
        INSERT INTO lachesis_notes (user_id, repo_name, issue, milestone, role, type, text)
        VALUES (:user_id, :repo_name, :issue, :milestone, :role, :type, :text)
        RETURNING {_NOTE_COLUMNS}
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "issue": issue,
        "milestone": milestone,
        "role": role,
        "type": note_type,
        "text": body,
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
        if row is None:
            raise RuntimeError(f"record_note returned no row for {repo_name!r}")
        _insert_event(conn, user_id, repo_name, "note_added", str(row["id"]))
    return dict(row)


def list_notes(
    user_id: str,
    repo_name: str,
    *,
    issue: int | None = None,
    milestone: str | None = None,
) -> list[dict[str, Any]]:
    """Return the notes on exactly one of *issue* or *milestone*, oldest first."""
    if (issue is None) == (milestone is None):
        raise ValueError("notes are read for exactly one issue or milestone")
    sql = text(
        f"""
        SELECT {_NOTE_COLUMNS}
        FROM lachesis_notes
        WHERE user_id = :user_id AND repo_name = :repo_name
          AND (issue = :issue OR milestone = :milestone)
        ORDER BY id
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "issue": issue, "milestone": milestone}
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).mappings().all()]


# ---------------------------------------------------------------------------
# lachesis_usage_reports
# ---------------------------------------------------------------------------

UsageKind = Literal["triage", "summarising", "audit", "tests", "implementation", "spec work"]


def record_usage_report(
    user_id: str,
    repo_name: str,
    issue: int,
    model: str,
    kind: UsageKind,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float | None,
    account: str | None = None,
) -> dict[str, Any]:
    """Store one run's usage on *issue* and its ``usage_reported`` event; *account* names
    the Claude account a run on an allowance drew on."""
    if kind not in get_args(UsageKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(UsageKind))}, got {kind!r}")
    sql = text(
        """
        INSERT INTO lachesis_usage_reports
            (user_id, repo_name, issue, model, kind, tokens_in, tokens_out, cost_usd, account)
        VALUES
            (:user_id, :repo_name, :issue, :model, :kind, :tokens_in, :tokens_out, :cost_usd,
             :account)
        RETURNING id, repo_name, issue, model, kind, tokens_in, tokens_out, cost_usd, account,
            created_at
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "issue": issue,
        "model": model,
        "kind": kind,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "account": account,
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
        if row is None:
            raise RuntimeError(f"record_usage_report returned no row for {repo_name!r}")
        _insert_event(conn, user_id, repo_name, "usage_reported", str(row["id"]))
    return dict(row)


def usage_by_issue(
    user_id: str, repo_name: str, by: Literal["kind", "model"] = "kind"
) -> dict[int, dict[str, int]]:
    """Return the tokens in and out reported on each issue in *repo_name*, by kind of work
    or, with *by* ``model``, by model."""
    sql = text(
        f"""
        SELECT issue, {by} AS key, SUM(tokens_in + tokens_out) AS tokens
        FROM lachesis_usage_reports
        WHERE user_id = :user_id AND repo_name = :repo_name
        GROUP BY issue, {by}
        """
    )
    with _engine().connect() as conn:
        rows = conn.execute(sql, {"user_id": user_id, "repo_name": repo_name}).mappings().all()
    usage: dict[int, dict[str, int]] = {}
    for row in rows:
        usage.setdefault(row["issue"], {})[row["key"]] = int(row["tokens"])
    return usage


# ---------------------------------------------------------------------------
# lachesis_run_outcomes
# ---------------------------------------------------------------------------

RunOutcome = Literal["rework", "retry", "audit_finding"]


def record_run_outcome(
    user_id: str, repo_name: str, issue: int, kind: UsageKind, outcome: RunOutcome
) -> dict[str, Any]:
    """Store *outcome* against the latest *kind* run on *issue* and its event."""
    if kind not in get_args(UsageKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(UsageKind))}, got {kind!r}")
    if outcome not in get_args(RunOutcome):
        raise ValueError(
            f"outcome must be one of {', '.join(get_args(RunOutcome))}, got {outcome!r}"
        )
    sql = text(
        """
        WITH run AS (
            SELECT id, model FROM lachesis_usage_reports
            WHERE user_id = :user_id AND repo_name = :repo_name AND issue = :issue
              AND kind = :kind
            ORDER BY id DESC LIMIT 1
        ), stored AS (
            INSERT INTO lachesis_run_outcomes (run_id, outcome)
            SELECT id, :outcome FROM run
            RETURNING id, run_id, outcome, created_at
        )
        SELECT stored.id, stored.run_id, run.model, stored.outcome, stored.created_at
        FROM stored JOIN run ON run.id = stored.run_id
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "issue": issue,
        "kind": kind,
        "outcome": outcome,
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
        if row is None:
            raise ValueError(
                f"no {kind} run on issue {issue} in {repo_name!r} to report {outcome} against"
            )
        _insert_event(conn, user_id, repo_name, "run_outcome_reported", str(row["id"]))
    return {"repo_name": repo_name, "issue": issue, "kind": kind, **dict(row)}


def list_runs(
    user_id: str, *, start: datetime | None = None, end: datetime | None = None
) -> list[dict[str, Any]]:
    """Return the account's runs with their count of each outcome and their issue's size
    (None while unsized), oldest first.

    With *start* or *end*, only the runs reported in ``[start, end)``.
    """
    sql = text(
        """
        SELECT r.id, r.repo_name, r.issue, r.model, r.kind,
               r.tokens_in, r.tokens_out, r.cost_usd, r.account, s.size,
               COUNT(o.id) FILTER (WHERE o.outcome = 'rework') AS rework,
               COUNT(o.id) FILTER (WHERE o.outcome = 'retry') AS retries,
               COUNT(o.id) FILTER (WHERE o.outcome = 'audit_finding') AS audit_findings
        FROM lachesis_usage_reports r
        LEFT JOIN lachesis_run_outcomes o ON o.run_id = r.id
        LEFT JOIN lachesis_issue_sizes s
            ON s.user_id = r.user_id AND s.repo_name = r.repo_name AND s.issue = r.issue
        WHERE r.user_id = :user_id
          AND (CAST(:start AS timestamptz) IS NULL OR r.created_at >= :start)
          AND (CAST(:end AS timestamptz) IS NULL OR r.created_at < :end)
        GROUP BY r.id, s.size
        ORDER BY r.id
        """
    )
    params = {"user_id": user_id, "start": start, "end": end}
    with _engine().connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    counted = ("tokens_in", "tokens_out", "rework", "retries", "audit_findings")
    return [{**row, **{column: int(row[column]) for column in counted}} for row in rows]


# ---------------------------------------------------------------------------
# lachesis_estimates
# ---------------------------------------------------------------------------

EstimateConfidence = Literal["low", "normal"]
EstimateBasis = Literal["kind", "all kinds", "none"]

_ESTIMATE_COLUMNS = (
    "repo_name, issue, kind, capability, tokens, confidence, basis, samples, estimated_at"
)


def set_estimate(
    user_id: str,
    repo_name: str,
    issue: int,
    kind: UsageKind,
    capability: str | None,
    tokens: int | None,
    confidence: EstimateConfidence,
    basis: EstimateBasis,
    samples: int,
) -> dict[str, Any]:
    """Replace *issue*'s token estimate with this one."""
    if kind not in get_args(UsageKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(UsageKind))}, got {kind!r}")
    sql = text(
        f"""
        INSERT INTO lachesis_estimates
            (user_id, repo_name, issue, kind, capability, tokens, confidence, basis, samples)
        VALUES
            (:user_id, :repo_name, :issue, :kind, :capability, :tokens, :confidence, :basis,
             :samples)
        ON CONFLICT (user_id, repo_name, issue) DO UPDATE
            SET kind = EXCLUDED.kind,
                capability = EXCLUDED.capability,
                tokens = EXCLUDED.tokens,
                confidence = EXCLUDED.confidence,
                basis = EXCLUDED.basis,
                samples = EXCLUDED.samples,
                estimated_at = now()
        RETURNING {_ESTIMATE_COLUMNS}
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "issue": issue,
        "kind": kind,
        "capability": capability,
        "tokens": tokens,
        "confidence": confidence,
        "basis": basis,
        "samples": samples,
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
    if row is None:
        raise RuntimeError(f"set_estimate returned no row for {repo_name!r} issue {issue}")
    return dict(row)


def list_estimates(user_id: str) -> list[dict[str, Any]]:
    """Return every stored issue estimate of the account."""
    sql = text(f"SELECT {_ESTIMATE_COLUMNS} FROM lachesis_estimates WHERE user_id = :user_id")
    with _engine().connect() as conn:
        rows = conn.execute(sql, {"user_id": user_id}).mappings().all()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# lachesis_issue_sizes
# ---------------------------------------------------------------------------

# Fibonacci points; 21 stands for "larger than 13", an issue to split before planning.
SIZES = (1, 2, 3, 5, 8, 13, 21)
TOO_BIG = 21


def check_size(size: int) -> None:
    """Refuse anything but a Fibonacci size or TOO_BIG."""
    if size not in SIZES:
        raise ValueError(
            f"size must be one of {', '.join(map(str, SIZES[:-1]))} points, or {TOO_BIG} for "
            f"an issue larger than 13 that must be split; got {size!r}"
        )


def set_size(user_id: str, repo_name: str, issue: int, size: int) -> dict[str, Any]:
    """Replace *issue*'s size with *size*."""
    check_size(size)
    sql = text(
        """
        INSERT INTO lachesis_issue_sizes (user_id, repo_name, issue, size)
        VALUES (:user_id, :repo_name, :issue, :size)
        ON CONFLICT (user_id, repo_name, issue) DO UPDATE
            SET size = EXCLUDED.size, sized_at = now()
        RETURNING repo_name, issue, size, sized_at
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "issue": issue, "size": size}
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
    if row is None:
        raise RuntimeError(f"set_size returned no row for {repo_name!r} issue {issue}")
    return dict(row)


def sizes_by_issue(user_id: str, repo_name: str | None = None) -> dict[tuple[str, int], int]:
    """Return every sized issue of the account as (repo, issue) -> size."""
    sql = "SELECT repo_name, issue, size FROM lachesis_issue_sizes WHERE user_id = :user_id"
    params: dict[str, Any] = {"user_id": user_id}
    if repo_name is not None:
        sql += " AND repo_name = :repo_name"
        params["repo_name"] = repo_name
    with _engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return {(row["repo_name"], int(row["issue"])): int(row["size"]) for row in rows}


# lachesis_audit_runs
# ---------------------------------------------------------------------------

AuditKind = Literal["architecture", "security", "clean-code", "tests", "requirements", "north-star"]
AuditOutcome = Literal["clean", "findings", "failed"]

_AUDIT_RUN_COLUMNS = (
    "id, repo_name, kind, capability, model, finished_at, outcome, issues, tokens_in, "
    "tokens_out, reported_at"
)


def record_audit_run(
    user_id: str,
    repo_name: str,
    kind: AuditKind,
    capability: str | None,
    model: str,
    finished_at: datetime,
    outcome: AuditOutcome,
    issues: list[int],
    tokens_in: int,
    tokens_out: int,
) -> dict[str, Any]:
    """Store one audit run."""
    if kind not in get_args(AuditKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(AuditKind))}, got {kind!r}")
    if outcome not in get_args(AuditOutcome):
        raise ValueError(
            f"outcome must be one of {', '.join(get_args(AuditOutcome))}, got {outcome!r}"
        )
    sql = text(
        f"""
        INSERT INTO lachesis_audit_runs
            (user_id, repo_name, kind, capability, model, finished_at, outcome, issues,
             tokens_in, tokens_out)
        VALUES
            (:user_id, :repo_name, :kind, :capability, :model, :finished_at, :outcome,
             :issues, :tokens_in, :tokens_out)
        RETURNING {_AUDIT_RUN_COLUMNS}
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "kind": kind,
        "capability": capability,
        "model": model,
        "finished_at": finished_at,
        "outcome": outcome,
        "issues": issues,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
    if row is None:
        raise RuntimeError(f"record_audit_run returned no row for {repo_name!r}")
    return _audit_run(row)


def list_audit_runs(user_id: str) -> list[dict[str, Any]]:
    """Return every audit run of the account, oldest first."""
    sql = text(
        f"SELECT {_AUDIT_RUN_COLUMNS} FROM lachesis_audit_runs WHERE user_id = :user_id "
        "ORDER BY finished_at, id"
    )
    with _engine().connect() as conn:
        rows = conn.execute(sql, {"user_id": user_id}).mappings().all()
    return [_audit_run(row) for row in rows]


def _audit_run(row: Any) -> dict[str, Any]:
    return {
        **row,
        "issues": [int(n) for n in row["issues"] or []],
        "tokens_in": int(row["tokens_in"]),
        "tokens_out": int(row["tokens_out"]),
    }


# ---------------------------------------------------------------------------
# lachesis_fuel
# ---------------------------------------------------------------------------

_FUEL_COLUMNS = """f.weekly_used_percent, f.weekly_resets_at, f.shorter_limit_until,
    f.five_hour_used_percent, f.reported_at"""

_ACCOUNT_COLUMNS = 'a.name, a.share_percent, a.five_hour_pause_percent, a.is_primary AS "primary"'


def register_account(
    user_id: str,
    name: str,
    share_percent: float,
    five_hour_pause_percent: float | None,
    primary: bool,
) -> dict[str, Any]:
    """Register the account *name*, or update it if it is registered.

    The first account a user registers is primary whatever *primary* says; making an
    account primary makes every other one not primary.
    """
    with _engine().begin() as conn:
        has_primary = conn.execute(
            text(
                "SELECT 1 FROM lachesis_accounts a WHERE a.user_id = :user_id"
                " AND a.is_primary AND a.name <> :name"
            ),
            {"user_id": user_id, "name": name},
        ).first()
        primary = primary or has_primary is None
        if primary:
            conn.execute(
                text(
                    "UPDATE lachesis_accounts SET is_primary = false"
                    " WHERE user_id = :user_id AND name <> :name"
                ),
                {"user_id": user_id, "name": name},
            )
        row = (
            conn.execute(
                text(
                    f"""
                    INSERT INTO lachesis_accounts AS a
                        (user_id, name, share_percent, five_hour_pause_percent, is_primary)
                    VALUES (:user_id, :name, :share_percent, :pause, :primary)
                    ON CONFLICT (user_id, name) DO UPDATE
                        SET share_percent = EXCLUDED.share_percent,
                            five_hour_pause_percent = EXCLUDED.five_hour_pause_percent,
                            is_primary = EXCLUDED.is_primary OR a.is_primary
                    RETURNING {_ACCOUNT_COLUMNS}
                    """
                ),
                {
                    "user_id": user_id,
                    "name": name,
                    "share_percent": share_percent,
                    "pause": five_hour_pause_percent,
                    "primary": primary,
                },
            )
            .mappings()
            .first()
        )
    if row is None:
        raise RuntimeError(f"register_account returned no row for {name!r}")
    return dict(row)


def list_accounts(user_id: str) -> list[dict[str, Any]]:
    """Return the user's registered accounts, the primary first, then by name."""
    sql = text(
        f"SELECT {_ACCOUNT_COLUMNS} FROM lachesis_accounts a WHERE a.user_id = :user_id"
        " ORDER BY a.is_primary DESC, a.name"
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"user_id": user_id}).mappings().all()]


def set_fuel(
    user_id: str,
    account: str,
    weekly_used_percent: float,
    weekly_resets_at: datetime,
    shorter_limit_until: datetime | None,
    five_hour_used_percent: float | None,
) -> dict[str, Any]:
    """Replace the fuel report of the registered *account* with this one."""
    sql = text(
        f"""
        WITH stored AS (
            INSERT INTO lachesis_fuel
                (user_id, account_id, weekly_used_percent, weekly_resets_at,
                 shorter_limit_until, five_hour_used_percent)
            SELECT :user_id, a.id, :weekly_used_percent, :weekly_resets_at,
                   :shorter_limit_until, :five_hour_used_percent
            FROM lachesis_accounts a
            WHERE a.user_id = :user_id AND a.name = :account
            ON CONFLICT (account_id) DO UPDATE
                SET weekly_used_percent = EXCLUDED.weekly_used_percent,
                    weekly_resets_at = EXCLUDED.weekly_resets_at,
                    shorter_limit_until = EXCLUDED.shorter_limit_until,
                    five_hour_used_percent = EXCLUDED.five_hour_used_percent,
                    reported_at = now()
            RETURNING *
        )
        SELECT {_FUEL_COLUMNS}, {_ACCOUNT_COLUMNS}
        FROM stored f JOIN lachesis_accounts a ON a.id = f.account_id
        """
    )
    params = {
        "user_id": user_id,
        "account": account,
        "weekly_used_percent": weekly_used_percent,
        "weekly_resets_at": weekly_resets_at,
        "shorter_limit_until": shorter_limit_until,
        "five_hour_used_percent": five_hour_used_percent,
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
    if row is None:
        raise RuntimeError(f"set_fuel returned no row for account {account!r}")
    return dict(row)


def list_fuel(user_id: str) -> list[dict[str, Any]]:
    """Return every registered account with its latest fuel report, the primary first.

    An account never reported has its fuel columns null.
    """
    sql = text(
        f"""
        SELECT {_ACCOUNT_COLUMNS}, {_FUEL_COLUMNS}
        FROM lachesis_accounts a LEFT JOIN lachesis_fuel f ON f.account_id = a.id
        WHERE a.user_id = :user_id
        ORDER BY a.is_primary DESC, a.name
        """
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"user_id": user_id}).mappings().all()]


def set_overflow(user_id: str, spent_usd: float, cap_usd: float) -> dict[str, Any]:
    """Replace the user's overflow report: the money spent through Requesty this sprint
    and the cap the author set there."""
    sql = text(
        """
        INSERT INTO lachesis_overflow (user_id, spent_usd, cap_usd)
        VALUES (:user_id, :spent_usd, :cap_usd)
        ON CONFLICT (user_id) DO UPDATE
            SET spent_usd = EXCLUDED.spent_usd,
                cap_usd = EXCLUDED.cap_usd,
                reported_at = now()
        RETURNING spent_usd, cap_usd, reported_at
        """
    )
    params = {"user_id": user_id, "spent_usd": spent_usd, "cap_usd": cap_usd}
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
    if row is None:
        raise RuntimeError(f"set_overflow returned no row for user_id={user_id!r}")
    return dict(row)


def get_overflow(user_id: str) -> dict[str, Any] | None:
    """Return the user's latest overflow report, or None if overflow was never reported."""
    sql = text(
        "SELECT spent_usd, cap_usd, reported_at FROM lachesis_overflow WHERE user_id = :user_id"
    )
    with _engine().connect() as conn:
        row = conn.execute(sql, {"user_id": user_id}).mappings().first()
    return None if row is None else dict(row)


def get_fuel(user_id: str) -> dict[str, Any] | None:
    """Return the primary account's latest fuel report with the overflow figures, which
    are None until overflow is reported, or None if the primary account was never
    reported."""
    primary = next((row for row in list_fuel(user_id) if row["primary"]), None)
    if primary is None or primary["reported_at"] is None:
        return None
    overflow = get_overflow(user_id)
    return {
        **primary,
        "overflow_spent_usd": overflow["spent_usd"] if overflow else None,
        "overflow_cap_usd": overflow["cap_usd"] if overflow else None,
    }


# ---------------------------------------------------------------------------
# lachesis_factory_tokens
# ---------------------------------------------------------------------------


def create_factory_token(user_id: str, name: str, token_hash: str) -> dict[str, Any]:
    """Store a factory token's hash under *name*; refused while an active one has the name."""
    sql = text(
        """
        INSERT INTO lachesis_factory_tokens (user_id, name, token_hash)
        VALUES (:user_id, :name, :token_hash)
        ON CONFLICT DO NOTHING
        RETURNING name, created_at
        """
    )
    with _engine().begin() as conn:
        row = (
            conn.execute(sql, {"user_id": user_id, "name": name, "token_hash": token_hash})
            .mappings()
            .first()
        )
    if row is None:
        raise ValueError(f"a factory token named {name!r} already exists; revoke it first")
    return dict(row)


def list_factory_tokens(user_id: str) -> list[dict[str, Any]]:
    """Return the user's active factory tokens by name, without their hashes."""
    sql = text(
        """
        SELECT name, created_at, last_used_at FROM lachesis_factory_tokens
        WHERE user_id = :user_id AND revoked_at IS NULL ORDER BY name
        """
    )
    with _engine().connect() as conn:
        return [dict(r) for r in conn.execute(sql, {"user_id": user_id}).mappings().all()]


def revoke_factory_token(user_id: str, name: str) -> bool:
    """Revoke the user's active factory token *name*; False when there is none."""
    sql = text(
        """
        UPDATE lachesis_factory_tokens SET revoked_at = now()
        WHERE user_id = :user_id AND name = :name AND revoked_at IS NULL
        RETURNING id
        """
    )
    with _engine().begin() as conn:
        return conn.execute(sql, {"user_id": user_id, "name": name}).first() is not None


def resolve_factory_token(token_hash: str) -> dict[str, Any] | None:
    """Return the user an active factory token acts for, recording its use, or None."""
    sql = text(
        """
        UPDATE lachesis_factory_tokens t SET last_used_at = now()
        FROM lachesis_users u
        WHERE t.token_hash = :token_hash AND t.revoked_at IS NULL AND u.id = t.user_id
        RETURNING u.id AS lachesis_uid, u.google_id, u.email
        """
    )
    with _engine().begin() as conn:
        row = conn.execute(sql, {"token_hash": token_hash}).mappings().first()
    return None if row is None else {**row, "lachesis_uid": str(row["lachesis_uid"])}


# ---------------------------------------------------------------------------
# lachesis_milestones
# ---------------------------------------------------------------------------

MilestoneState = Literal["planned", "active", "verifying", "done", "dropped"]

_MILESTONE_SELECT = """
    SELECT m.id, m.repo_name, m.title, m.goal, m.created_at, m.started_at, m.dropped_at,
           COALESCE(
               (SELECT array_agg(s.spec_change ORDER BY s.spec_change)
                FROM lachesis_milestone_spec_changes s
                WHERE s.milestone_id = m.id),
               CAST('{}' AS integer[])
           ) AS spec_changes
    FROM lachesis_milestones m
    WHERE m.user_id = :user_id AND m.repo_name = :repo_name
"""


def _select_milestone(
    conn: Connection, user_id: str, repo_name: str, milestone_id: int
) -> dict[str, Any] | None:
    sql = text(_MILESTONE_SELECT + " AND m.id = :id")
    params = {"user_id": user_id, "repo_name": repo_name, "id": milestone_id}
    row = conn.execute(sql, params).mappings().first()
    return dict(row) if row is not None else None


def _require_milestone(
    conn: Connection, user_id: str, repo_name: str, milestone_id: int
) -> dict[str, Any]:
    row = _select_milestone(conn, user_id, repo_name, milestone_id)
    if row is None:
        raise ValueError(f"Milestone {milestone_id} not found in {repo_name}")
    return row


def _add_spec_change(
    conn: Connection, user_id: str, repo_name: str, milestone_id: int, spec_change: int
) -> None:
    """Put *spec_change* in the milestone, refusing one already in any milestone.

    The check names the milestone holding it; the unique constraint backs it
    against a concurrent add.
    """
    held_sql = text(
        """
        SELECT m.id, m.title, m.dropped_at
        FROM lachesis_milestone_spec_changes s
        JOIN lachesis_milestones m ON m.id = s.milestone_id
        WHERE s.user_id = :user_id AND s.repo_name = :repo_name
          AND s.spec_change = :spec_change
        """
    )
    insert_sql = text(
        """
        INSERT INTO lachesis_milestone_spec_changes (milestone_id, user_id, repo_name, spec_change)
        SELECT id, user_id, repo_name, :spec_change
        FROM lachesis_milestones
        WHERE id = :id AND user_id = :user_id AND repo_name = :repo_name
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "id": milestone_id,
        "spec_change": spec_change,
    }
    held = conn.execute(held_sql, params).mappings().first()
    if held is not None:
        if held["id"] == milestone_id:
            raise ValueError(f"#{spec_change} is already in milestone {milestone_id}")
        dropped = " (dropped)" if held["dropped_at"] is not None else ""
        raise ValueError(
            f"#{spec_change} is already in milestone {held['id']} ({held['title']}){dropped}; "
            "a spec change belongs to at most one milestone"
        )
    conn.execute(insert_sql, params)
    _insert_event(
        conn, user_id, repo_name, "milestone_spec_change_added", f"{milestone_id}:{spec_change}"
    )


def create_milestone(
    user_id: str, repo_name: str, title: str, goal: str, spec_changes: list[int]
) -> dict[str, Any]:
    """Store a planned milestone holding *spec_changes*, with its events, in one transaction."""
    sql = text(
        """
        INSERT INTO lachesis_milestones (user_id, repo_name, title, goal)
        VALUES (:user_id, :repo_name, :title, :goal)
        RETURNING id
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "title": title, "goal": goal}
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
        if row is None:
            raise RuntimeError(f"create_milestone returned no row for {repo_name!r}")
        milestone_id = row["id"]
        _insert_event(conn, user_id, repo_name, "milestone_created", str(milestone_id))
        for spec_change in spec_changes:
            _add_spec_change(conn, user_id, repo_name, milestone_id, spec_change)
        return _require_milestone(conn, user_id, repo_name, milestone_id)


def get_milestone(user_id: str, repo_name: str, milestone_id: int) -> dict[str, Any] | None:
    """Return milestone *milestone_id* in *repo_name* with its spec changes, or ``None``."""
    with _engine().connect() as conn:
        return _select_milestone(conn, user_id, repo_name, milestone_id)


def list_milestones(user_id: str, repo_name: str) -> list[dict[str, Any]]:
    """Return every milestone in *repo_name* with its spec changes, oldest first."""
    sql = text(_MILESTONE_SELECT + " ORDER BY m.id")
    params = {"user_id": user_id, "repo_name": repo_name}
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).mappings().all()]


def start_milestone(user_id: str, repo_name: str, milestone_id: int) -> dict[str, Any]:
    """Start a planned milestone and record ``milestone_started``.

    Raises ``ValueError`` unless the milestone is in *repo_name* and planned.
    """
    sql = text(
        """
        UPDATE lachesis_milestones
        SET started_at = now()
        WHERE user_id = :user_id AND repo_name = :repo_name AND id = :id
          AND started_at IS NULL AND dropped_at IS NULL
        RETURNING id
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "id": milestone_id}
    with _engine().begin() as conn:
        if conn.execute(sql, params).mappings().first() is None:
            row = _require_milestone(conn, user_id, repo_name, milestone_id)
            why = "dropped" if row["dropped_at"] is not None else "already started"
            raise ValueError(f"Milestone {milestone_id} is {why}; only a planned one can start")
        _insert_event(conn, user_id, repo_name, "milestone_started", str(milestone_id))
        return _require_milestone(conn, user_id, repo_name, milestone_id)


def drop_milestone(user_id: str, repo_name: str, milestone_id: int) -> dict[str, Any]:
    """Drop a milestone and record ``milestone_dropped``. Its spec changes stay in it.

    Raises ``ValueError`` unless the milestone is in *repo_name* and not dropped.
    Refusing a done milestone is the caller's: done is read from the work.
    """
    sql = text(
        """
        UPDATE lachesis_milestones
        SET dropped_at = now()
        WHERE user_id = :user_id AND repo_name = :repo_name AND id = :id
          AND dropped_at IS NULL
        RETURNING id
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "id": milestone_id}
    with _engine().begin() as conn:
        if conn.execute(sql, params).mappings().first() is None:
            _require_milestone(conn, user_id, repo_name, milestone_id)
            raise ValueError(f"Milestone {milestone_id} is already dropped")
        _insert_event(conn, user_id, repo_name, "milestone_dropped", str(milestone_id))
        return _require_milestone(conn, user_id, repo_name, milestone_id)


def _lock_milestone(
    conn: Connection, user_id: str, repo_name: str, milestone_id: int
) -> dict[str, Any]:
    sql = text(
        """
        SELECT id, dropped_at
        FROM lachesis_milestones
        WHERE user_id = :user_id AND repo_name = :repo_name AND id = :id
        FOR UPDATE
        """
    )
    params = {"user_id": user_id, "repo_name": repo_name, "id": milestone_id}
    row = conn.execute(sql, params).mappings().first()
    if row is None:
        raise ValueError(f"Milestone {milestone_id} not found in {repo_name}")
    return dict(row)


def add_milestone_spec_change(
    user_id: str, repo_name: str, milestone_id: int, spec_change: int
) -> dict[str, Any]:
    """Add *spec_change* to a milestone that is not dropped, and record the event.

    Raises ``ValueError`` when the spec change is already in any milestone.
    Refusing a done milestone is the caller's: done is read from the work.
    """
    with _engine().begin() as conn:
        if _lock_milestone(conn, user_id, repo_name, milestone_id)["dropped_at"] is not None:
            raise ValueError(f"Milestone {milestone_id} is dropped; spec changes cannot join it")
        _add_spec_change(conn, user_id, repo_name, milestone_id, spec_change)
        return _require_milestone(conn, user_id, repo_name, milestone_id)


def remove_milestone_spec_change(
    user_id: str, repo_name: str, milestone_id: int, spec_change: int
) -> dict[str, Any]:
    """Take *spec_change* out of a milestone and record the event.

    Refusing a done milestone is the caller's: done is read from the work.
    """
    sql = text(
        """
        DELETE FROM lachesis_milestone_spec_changes
        WHERE user_id = :user_id AND repo_name = :repo_name
          AND milestone_id = :id AND spec_change = :spec_change
        RETURNING spec_change
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "id": milestone_id,
        "spec_change": spec_change,
    }
    with _engine().begin() as conn:
        _lock_milestone(conn, user_id, repo_name, milestone_id)
        if conn.execute(sql, params).mappings().first() is None:
            raise ValueError(f"#{spec_change} is not in milestone {milestone_id}")
        _insert_event(
            conn,
            user_id,
            repo_name,
            "milestone_spec_change_removed",
            f"{milestone_id}:{spec_change}",
        )
        return _require_milestone(conn, user_id, repo_name, milestone_id)


def record_milestone_state(
    user_id: str, repo_name: str, milestone_id: int, state: MilestoneState
) -> bool:
    """Record ``milestone_state_changed`` when *state* differs from the last one recorded.

    The event log is the record: its subject is ``"<id>:<state>"``, and a started
    milestone with none yet was last ``active``, which ``milestone_started``
    announced. Locking the milestone serialises concurrent reads deriving it.
    Returns whether an event was recorded.
    """
    last_sql = text(
        """
        SELECT subject
        FROM lachesis_events
        WHERE user_id = :user_id AND repo_name = :repo_name
          AND kind = 'milestone_state_changed' AND subject LIKE :prefix
        ORDER BY id DESC
        LIMIT 1
        """
    )
    prefix = f"{milestone_id}:"
    params = {"user_id": user_id, "repo_name": repo_name, "prefix": prefix + "%"}
    with _engine().begin() as conn:
        _lock_milestone(conn, user_id, repo_name, milestone_id)
        last = conn.execute(last_sql, params).mappings().first()
        last_state = str(last["subject"]).removeprefix(prefix) if last else "active"
        if last_state == state:
            return False
        _insert_event(conn, user_id, repo_name, "milestone_state_changed", prefix + state)
        return True


def milestones_done_between(user_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Return each milestone first found done in ``[start, end)``, with when."""
    sql = text(
        """
        SELECT DISTINCT ON (m.id) m.repo_name, m.id, m.title, e.created_at AS done_at
        FROM lachesis_events e
        JOIN lachesis_milestones m
          ON m.user_id = e.user_id AND m.repo_name = e.repo_name
         AND e.subject = m.id::text || :done_suffix
        WHERE e.user_id = :user_id AND e.kind = 'milestone_state_changed'
          AND e.created_at >= :start AND e.created_at < :end
        ORDER BY m.id, e.created_at
        """
    )
    params = {"user_id": user_id, "start": start, "end": end, "done_suffix": ":done"}
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).mappings().all()]


# ---------------------------------------------------------------------------
# lachesis_sprint_reports
# ---------------------------------------------------------------------------

_SPRINT_REPORT_COLUMNS = "sprint_start, sprint_end, report, created_at"


def get_sprint_report(user_id: str, sprint_end: datetime) -> dict[str, Any] | None:
    """Return the report on the sprint ending at *sprint_end*, or None if it is not reported."""
    sql = text(
        f"""
        SELECT {_SPRINT_REPORT_COLUMNS}
        FROM lachesis_sprint_reports
        WHERE user_id = :user_id AND sprint_end = :sprint_end
        """
    )
    params = {"user_id": user_id, "sprint_end": sprint_end}
    with _engine().connect() as conn:
        row = conn.execute(sql, params).mappings().first()
    return None if row is None else dict(row)


def claim_sprint_report(
    user_id: str, sprint_start: datetime, sprint_end: datetime, report: dict[str, Any]
) -> dict[str, Any] | None:
    """Store *report* as the sprint's only report and return it; None if one was stored first.

    Storing it is the claim on the sprint's one notification. The absent row is
    the signal that the sprint was already reported, not an error, so unlike the
    other writers here an empty RETURNING never raises.
    """
    sql = text(
        f"""
        INSERT INTO lachesis_sprint_reports (user_id, sprint_start, sprint_end, report)
        VALUES (:user_id, :sprint_start, :sprint_end, CAST(:report AS jsonb))
        ON CONFLICT (user_id, sprint_end) DO NOTHING
        RETURNING {_SPRINT_REPORT_COLUMNS}
        """
    )
    params = {
        "user_id": user_id,
        "sprint_start": sprint_start,
        "sprint_end": sprint_end,
        "report": json.dumps(report, default=str),
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
    return None if row is None else dict(row)


# ---------------------------------------------------------------------------
# lachesis_experiments
# ---------------------------------------------------------------------------

ExperimentVerdict = Literal["kept", "reverted", "extended"]

_EXPERIMENT_COLUMNS = (
    "id, repo_name, title, change, measure, baseline, target, issues, state, results, created_at"
)


def create_experiment(
    user_id: str,
    repo_name: str,
    title: str,
    change: str,
    measure: str,
    baseline: float,
    target: float,
    issues: list[int],
) -> dict[str, Any]:
    """Store an open experiment with no results and return it."""
    sql = text(
        f"""
        INSERT INTO lachesis_experiments
            (user_id, repo_name, title, change, measure, baseline, target, issues)
        VALUES (:user_id, :repo_name, :title, :change, :measure, :baseline, :target, :issues)
        RETURNING {_EXPERIMENT_COLUMNS}
        """
    )
    params = {
        "user_id": user_id,
        "repo_name": repo_name,
        "title": title,
        "change": change,
        "measure": measure,
        "baseline": baseline,
        "target": target,
        "issues": issues,
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
    if row is None:
        raise RuntimeError(f"create_experiment returned no row for {repo_name!r}")
    return dict(row)


def list_open_experiments(user_id: str) -> list[dict[str, Any]]:
    """Return the account's open experiments, oldest first."""
    sql = text(
        f"""
        SELECT {_EXPERIMENT_COLUMNS}
        FROM lachesis_experiments
        WHERE user_id = :user_id AND state = 'open'
        ORDER BY id
        """
    )
    with _engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"user_id": user_id}).mappings().all()]


def judge_experiment(
    user_id: str,
    experiment_id: int,
    value: float,
    verdict: ExperimentVerdict,
    reason: str,
    judged_at: datetime,
) -> dict[str, Any]:
    """Append the judgement to an open experiment's results and return the experiment.

    ``kept`` and ``reverted`` close it; ``extended`` leaves it open, and only once.
    Raises ``ValueError`` for an experiment that is not found, not open, or already
    extended when *verdict* is ``extended``.
    """
    if verdict not in get_args(ExperimentVerdict):
        raise ValueError(
            f"verdict must be one of {', '.join(get_args(ExperimentVerdict))}, got {verdict!r}"
        )
    sql = text(
        f"""
        UPDATE lachesis_experiments
        SET state = :state, results = results || CAST(:result AS jsonb)
        WHERE user_id = :user_id AND id = :id AND state = 'open'
          AND (:verdict <> 'extended' OR NOT results @> '[{{"verdict": "extended"}}]')
        RETURNING {_EXPERIMENT_COLUMNS}
        """
    )
    result = {"value": value, "verdict": verdict, "reason": reason, "judged_at": judged_at}
    params = {
        "user_id": user_id,
        "id": experiment_id,
        "state": "open" if verdict == "extended" else verdict,
        "verdict": verdict,
        "result": json.dumps([result], default=str),
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
        if row is not None:
            return dict(row)
        found = (
            conn.execute(
                text(
                    "SELECT state FROM lachesis_experiments WHERE user_id = :user_id AND id = :id"
                ),
                {"user_id": user_id, "id": experiment_id},
            )
            .mappings()
            .first()
        )
    if found is None:
        raise ValueError(f"Experiment {experiment_id} not found")
    if found["state"] != "open":
        raise ValueError(f"Experiment {experiment_id} is already {found['state']}")
    raise ValueError(
        f"Experiment {experiment_id} was already given one more sprint: keep or revert it"
    )
