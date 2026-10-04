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
  lachesis_events         — append-only log of changes to the factory's records
  lachesis_questions      — questions for the author and the issues each one blocks
  lachesis_milestones     — milestones and when the author started or dropped them
  lachesis_milestone_spec_changes — the spec changes each milestone groups, one milestone each

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
) -> dict[str, Any]:
    """Store a question and its ``question_asked`` event in one transaction."""
    if kind not in get_args(QuestionKind):
        raise ValueError(f"kind must be one of {', '.join(get_args(QuestionKind))}, got {kind!r}")
    sql = text(
        """
        INSERT INTO lachesis_questions (
            user_id, repo_name, question, context, options, kind, asker,
            capability, spec_change, milestone, issue, blocks
        )
        VALUES (
            :user_id, :repo_name, :question, :context, :options, :kind, :asker,
            :capability, :spec_change, :milestone, :issue, :blocks
        )
        RETURNING id, repo_name, question, context, options, kind, asker,
                  capability, spec_change, milestone, issue, blocks, created_at
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
    }
    with _engine().begin() as conn:
        row = conn.execute(sql, params).mappings().first()
        if row is None:
            raise RuntimeError(f"record_question returned no row for {repo_name!r}")
        _insert_event(conn, user_id, repo_name, "question_asked", str(row["id"]))
    return dict(row)


OutcomeKind = Literal["none", "standing_rule", "spec_change", "new_project"]

_QUESTION_COLUMNS = """id, repo_name, question, context, options, kind, asker,
                  capability, spec_change, milestone, issue, blocks, created_at,
                  answer, answerer, answered_at, outcome"""


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

    Returns the answered question with ``released``: the issues it blocked that no
    other open question still blocks. Raises ``ValueError`` unless the question is
    in *repo_name* and still open.
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
          AND answered_at IS NULL
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
